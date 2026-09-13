"""Itinerary orchestration: request -> ranked pool -> daily clusters -> schedules.

Pipeline, in order, with the reason each step exists:

1. **Select** a ranked candidate pool (`candidates`). Oversampled, because the
   clusterer and scheduler both need alternatives to choose from.
2. **Cluster** geographically into one group per day (`cluster`). Without this,
   a day zigzags across the city; with plain KMeans, days come out wildly
   unbalanced, so the clustering is capacity-constrained.
3. **Schedule** each day (`schedule`): time-aware greedy construction that
   treats opening hours as hard constraints, then 2-opt improvement validated
   against the schedule rather than against raw distance.

Day *assignment* happens before scheduling, which is a real simplification: a
place closed on the Monday it was clustered into is dropped rather than moved to
Tuesday. `_reassign_dropped` mitigates the worst of it by offering a day's
unplaced stops to later days, but full cross-day reassignment would mean solving
all days jointly. That is noted as a limitation rather than hidden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.config import CityConfig
from app.ingestion.normalize import haversine_m
from app.models.enums import PACE_PROFILES, Pace
from app.optimizer.candidates import (
    CandidateRequest,
    ScoredCandidate,
    pool_size_for,
    select_candidates,
)
from app.optimizer.cluster import GeoPoint, cluster_by_day, medoid, order_days
from app.optimizer.schedule import (
    DEFAULT_MAX_LEG_MINUTES,
    MAX_LEG_MINUTES,
    MEAL_CATEGORIES,
    DayPlan,
    Stop,
    schedule_day,
)
from app.optimizer.travel import TravelMode, TravelTimeProvider, build_provider

log = logging.getLogger(__name__)

#: Candidates kept per day, as a multiple of that day's stop count. See the
#: quality-gate comment in `build_itinerary`.
SHORTLIST_MULTIPLIER = 2

#: Composite-score points charged per kilometre from the day's medoid when
#: shortlisting. This is what stops a far outlier from being scheduled between
#: two downtown stops: Parque de El Capricho (score 68, 9km from the museum
#: cluster's medoid) is charged 36 points and drops below the Prado's 75, where
#: score alone had it winning and producing a 2h24m walking leg.
COMPACTNESS_PENALTY_PER_KM = 4.0


@dataclass(slots=True)
class Itinerary:
    city: str
    start_date: date
    pace: Pace
    days: list[DayPlan] = field(default_factory=list)
    #: Candidates that never made it into any day, with why.
    unused: list[tuple[Stop, str]] = field(default_factory=list)
    provider_name: str = ""
    travel_mode: str = ""
    requested_tags: tuple[str, ...] = ()
    max_price_tier: int = 4
    pool_size: int = 0

    @property
    def total_stops(self) -> int:
        return sum(len(d.stops) for d in self.days)

    @property
    def total_travel_minutes(self) -> float:
        return sum(d.total_travel_seconds for d in self.days) / 60.0


def _shortlist_with_meal_quota(
    ordered: list[ScoredCandidate], size: int, meals: int
) -> list[ScoredCandidate]:
    """Take the top `size` candidates, but guarantee room for `meals` eateries.

    A pure top-N cut regularly produced a day with no restaurant in the
    shortlist at all — sights outrank a good tapas bar on composite score — and
    then the scheduler could not place a meal it was never offered. This
    promotes the best-ranked meal candidates into the shortlist, displacing the
    weakest non-meal ones.
    """
    head = ordered[:size]
    if meals <= 0:
        return head

    present = sum(1 for c in head if c.stop.category in MEAL_CATEGORIES)
    missing = meals - present
    if missing <= 0:
        return head

    promotions = [
        c for c in ordered[size:] if c.stop.category in MEAL_CATEGORIES
    ][:missing]
    if not promotions:
        return head

    # Drop the weakest non-meal entries to make room, keeping order otherwise.
    keep = list(head)
    for _ in promotions:
        for index in range(len(keep) - 1, -1, -1):
            if keep[index].stop.category not in MEAL_CATEGORIES:
                keep.pop(index)
                break
    return keep + promotions


def build_itinerary(
    session: Session,
    *,
    city: CityConfig,
    start_date: date,
    days: int,
    pace: Pace = Pace.MODERATE,
    tags: tuple[str, ...] = (),
    avoid_tags: tuple[str, ...] = (),
    max_price_tier: int = 4,
    center: tuple[float, float] | None = None,
    radius_m: float | None = None,
    provider: TravelTimeProvider | None = None,
    settings=None,
) -> Itinerary:
    if days <= 0:
        raise ValueError("days must be positive")

    profile = PACE_PROFILES[pace]
    stops_per_day = profile["stops_per_day"]

    if provider is None:
        from app.config import get_settings

        settings = settings or get_settings()
        mode = TravelMode(getattr(settings, "travel_mode", "walk"))
        provider = build_provider(settings, mode)
    mode_name = getattr(provider, "mode", None) or getattr(
        getattr(provider, "inner", None), "mode", TravelMode.WALK
    )

    request = CandidateRequest(
        city=city.name,
        preferred_tags=tags,
        avoid_tags=avoid_tags,
        max_price_tier=max_price_tier,
        center=center,
        radius_m=radius_m,
        limit=pool_size_for(days, stops_per_day),
    )
    candidates = select_candidates(session, request)

    itinerary = Itinerary(
        city=city.name,
        start_date=start_date,
        pace=pace,
        provider_name=getattr(provider, "name", "unknown"),
        travel_mode=str(mode_name),
        requested_tags=tags,
        max_price_tier=max_price_tier,
        pool_size=len(candidates),
    )
    if not candidates:
        log.warning("no candidates matched the request; returning empty itinerary")
        itinerary.days = [
            DayPlan(
                day_index=i,
                day_date=start_date + timedelta(days=i),
                weekday=(start_date + timedelta(days=i)).weekday(),
            )
            for i in range(days)
        ]
        return itinerary

    by_key = {c.stop.key: c for c in candidates}
    points = [
        GeoPoint(key=c.stop.key, lat=c.stop.lat, lng=c.stop.lng, score=c.boosted_score)
        for c in candidates
    ]

    # Cluster with headroom: the scheduler will reject some stops on hours, so a
    # day whose cluster holds exactly `stops_per_day` would come out short.
    capacity = max(stops_per_day + 2, (len(points) + days - 1) // days)
    groups = order_days(cluster_by_day(points, days, capacity))

    # Quality gate before scheduling. Without it the scheduler optimises a route
    # over the whole cluster — up to 20 places, most of them filler — and
    # geography wins over quality, so a day ends up at a cinema while the Prado
    # goes unvisited and one leg runs to two hours. Keeping a modest multiple of
    # what the day can hold leaves real choice without the filler.
    shortlist_size = stops_per_day * SHORTLIST_MULTIPLIER + 2

    carried: list[ScoredCandidate] = []
    for index, group in enumerate(groups):
        day_date = start_date + timedelta(days=index)
        anchor = medoid(group)

        # `anchor` is bound as a default so the closure captures this day's
        # medoid rather than the loop variable.
        def shortlist_rank(candidate, _anchor: GeoPoint | None = anchor) -> float:
            if _anchor is None:
                return -candidate.boosted_score
            km = (
                haversine_m(candidate.stop.lat, candidate.stop.lng, _anchor.lat, _anchor.lng)
                / 1000.0
            )
            return -(candidate.boosted_score - COMPACTNESS_PENALTY_PER_KM * km)

        ordered = sorted((by_key[p.key] for p in group), key=shortlist_rank)
        ranked = _shortlist_with_meal_quota(
            ordered, shortlist_size, profile.get("meals_per_day", 2)
        )
        group_candidates = ranked + carried
        carried = []
        shortlisted = {c.stop.key for c in ranked}
        for point in group:
            if point.key not in shortlisted:
                itinerary.unused.append(
                    (by_key[point.key].stop, "outranked by stronger places in the same area")
                )

        plan = schedule_day(
            index,
            day_date,
            [c.stop for c in group_candidates],
            provider,
            day_start_hour=profile["day_start_hour"],
            day_end_hour=profile["day_end_hour"],
            max_stops=stops_per_day,
            max_leg_minutes=MAX_LEG_MINUTES.get(str(mode_name), DEFAULT_MAX_LEG_MINUTES),
        )
        itinerary.days.append(plan)

        # Offer anything closed today to the following day. Partial mitigation
        # of pre-scheduling day assignment; see the module docstring.
        placed = {s.stop.key for s in plan.stops}
        for stop, reason in plan.dropped:
            if stop.key in placed:
                continue
            if index + 1 < days and "closed" in reason:
                carried.append(by_key[stop.key])
            else:
                itinerary.unused.append((stop, reason))

    for leftover in carried:
        itinerary.unused.append((leftover.stop, "no remaining day could accommodate it"))

    log.info(
        "itinerary: %d stops across %d days, %.0f min travel (provider=%s)",
        itinerary.total_stops,
        len(itinerary.days),
        itinerary.total_travel_minutes,
        itinerary.provider_name,
    )
    return itinerary
