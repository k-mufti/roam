"""Day scheduling with opening hours as hard constraints.

## Why ordering and scheduling can't be separated

The obvious pipeline is "order the stops to minimise travel, then assign times".
It doesn't work, because feasibility depends on order. A bar that opens at 19:00
and a museum that closes at 20:00 can both be visited — but only in one
sequence. A shortest tour can be an impossible schedule, and the spec's own
example ("don't schedule a nightclub at 10am") is exactly this failure.

So construction is **time-aware from the start**: a greedy pass that, at each
step, considers only stops that are actually open when you could arrive, and
picks the cheapest by travel plus waiting. 2-opt then improves the result, but
every candidate reordering is re-simulated and rejected if it drops a stop.

## Waiting versus free time

Arriving before a place opens is allowed, but two very different things were
initially conflated under one label. A 12-minute wait for a museum to open is
*waiting*: you stand there. A 5-hour gap because the next restaurant does not
serve dinner until 20:00 is *free time*: you go and do something else, and the
walk happens later.

Reporting both as "waited 299m" was simply wrong, and it distorted the
optimiser, which penalised unavoidable afternoon gaps as heavily as genuine
queueing. Gaps above `FREE_TIME_THRESHOLD_MINUTES` are recorded as free time
before the stop, with travel deferred to just before opening, and are charged at
a much lower rate.

## Unknown hours get a category prior, not a blank cheque

`app.models.hours` treats missing hours as permissive, because refusing to
schedule everything we lack data for would gut the itinerary. That is right for
a museum or a park, and wrong for a bar: the scraped sources supply no hours at
all, so a flamenco dinner venue was cheerfully booked at 10:26.

So when a stop has no hours, a **category default window** applies instead of
"any time". It is a prior, not data — `ScheduledStop.hours_assumed` records that
the time was chosen from a category default so the UI can say so rather than
implying the place confirmed it.

## Composition limits

Without them the optimiser produces five consecutive tapas bars, which is
technically optimal and useless. Two mechanisms:

* **Daily caps** — two restaurants (lunch and dinner), one nightlife venue.
* **Minimum spacing between same-category stops** — a cap alone permitted
  13:00 lunch followed immediately by 14:21 lunch, which satisfies "two
  restaurants" and is nonsense. Restaurants must be four hours apart, which
  naturally separates them into lunch and dinner.

Spacing is enforced inside `simulate`, not only during construction, because
`simulate` is the single definition of a valid day — 2-opt validates candidate
reorderings through it, so a rule that lived only in the greedy pass could be
undone by the improvement pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, time
from typing import Any

from app.ingestion.normalize import haversine_m
from app.models.enums import DEFAULT_DWELL_MINUTES, PlaceCategory
from app.models.hours import (
    all_week,
    earliest_open,
    intervals_for,
    is_open_during,
    minutes_to_time,
)
from app.optimizer.travel import LatLng, TravelTimeProvider

log = logging.getLogger(__name__)

#: Cost of one minute of waiting, relative to one minute of travelling.
WAIT_PENALTY = 0.8
#: A gap longer than this is free time in the traveller's day, not a wait.
FREE_TIME_THRESHOLD_MINUTES = 45
#: Free time is a mild inefficiency, not an annoyance, so it is charged lightly.
FREE_TIME_PENALTY = 0.15
#: Extra cost for visiting two places of the same category back to back.
SAME_CATEGORY_PENALTY_SECONDS = 900.0
#: Seconds of travel the greedy will accept per point of composite score.
SCORE_TO_SECONDS = 30.0

#: Madrid meal windows, as (start_minute, end_minute). Lunch really does start
#: at 13:00-14:00 here and dinner rarely before 21:00; using northern-European
#: times would schedule "dinner" while every kitchen is shut.
MEAL_WINDOWS: tuple[tuple[int, int], ...] = ((13 * 60, 15 * 60 + 30), (20 * 60, 22 * 60 + 30))
#: Cost discount for taking a meal stop during a meal window. Large, because a
#: day without lunch is a worse itinerary than a day with 15 extra minutes of
#: walking.
MEAL_WINDOW_BONUS_SECONDS = 2400.0
#: How early the bonus may apply, relative to the window opening. The bonus is
#: tested against *arrival*, not against the scheduled start: tested against the
#: start, a 2400s discount beat the free-time penalty from four hours of idling,
#: and the optimiser opened Day 1 with "09:00: wait until lunch". The bonus must
#: mean "it is lunchtime, eat now", not "wait four hours for lunch".
MEAL_BONUS_LEAD_MINUTES = 30
#: Categories that count as a meal.
MEAL_CATEGORIES = frozenset({PlaceCategory.RESTAURANT})

#: Hardest cap on a single leg, by travel mode, in minutes. No traveller walks
#: 2h24m between two stops — which is exactly what the optimiser produced when
#: KMeans absorbed Parque de El Capricho (11km out) into the downtown museum
#: cluster. The compactness-aware shortlist in `itinerary` usually prevents
#: this; the cap is the backstop.
MAX_LEG_MINUTES: dict[str, int] = {"walk": 45, "transit": 60, "drive": 45}
DEFAULT_MAX_LEG_MINUTES = 45

#: Two stops closer together than this are effectively the same location, and
#: scheduling both wastes an hour of the day. This is not a dedup failure: the
#: scraped sources carry aggregate listings ("Museum Triangle", "Paseo del
#: Prado") that legitimately name an *area* containing places we also have
#: individually, 9m apart with an unrelated name. Entity resolution correctly
#: keeps them separate; the scheduler declines to visit both.
MIN_STOP_SEPARATION_M = 90.0

#: Hard caps per day, by category. Absent categories are unlimited.
CATEGORY_DAILY_LIMITS: dict[PlaceCategory, int] = {
    PlaceCategory.RESTAURANT: 2,
    PlaceCategory.NIGHTLIFE: 1,
    PlaceCategory.CAFE: 2,
    PlaceCategory.BAR: 2,
    # Three allows Madrid's actual Golden Triangle (Prado, Reina Sofia and
    # Thyssen sit within 1km of each other) without a seven-museum death march.
    PlaceCategory.MUSEUM: 3,
}

#: Default open windows by category, applied ONLY when a place has no hours
#: data. These encode Madrid norms: lunch is 13:00-16:00 and dinner starts at
#: 20:00, bars are evening venues, clubs open at 23:00.
CATEGORY_DEFAULT_WINDOWS: dict[PlaceCategory, list[tuple[str, str]]] = {
    PlaceCategory.RESTAURANT: [("13:00", "16:00"), ("20:00", "23:30")],
    PlaceCategory.CAFE: [("08:30", "20:00")],
    PlaceCategory.BAR: [("18:00", "02:00")],
    PlaceCategory.NIGHTLIFE: [("23:00", "06:00")],
    PlaceCategory.MUSEUM: [("10:00", "20:00")],
    PlaceCategory.SHOPPING: [("10:00", "21:00")],
    PlaceCategory.ATTRACTION: [("09:00", "21:00")],
    PlaceCategory.PARK: [("07:00", "22:00")],
    # OTHER deliberately absent: with no category signal there is no defensible
    # prior, so such places stay permissive.
}

#: Minimum minutes between the *starts* of two same-category stops. Absent
#: categories may repeat freely. Four hours for restaurants is what separates
#: lunch from dinner; without it the scheduler booked both at lunchtime.
MIN_SAME_CATEGORY_GAP_MINUTES: dict[PlaceCategory, int] = {
    PlaceCategory.RESTAURANT: 240,
    PlaceCategory.CAFE: 180,
    PlaceCategory.BAR: 120,
}


@dataclass(frozen=True, slots=True)
class Stop:
    """A candidate stop, decoupled from the ORM."""

    key: str
    name: str
    lat: float
    lng: float
    category: PlaceCategory
    score: float = 0.0
    price_tier: int | None = None
    tags: tuple[str, ...] = ()
    hours: dict[str, Any] | None = None
    address: str | None = None
    dwell_minutes: int | None = None

    @property
    def dwell(self) -> int:
        return self.dwell_minutes or DEFAULT_DWELL_MINUTES.get(self.category, 60)

    @property
    def has_real_hours(self) -> bool:
        return bool(self.hours)

    @property
    def point(self) -> LatLng:
        return LatLng(self.lat, self.lng)


@dataclass(slots=True)
class ScheduledStop:
    stop: Stop
    arrival_minute: int
    start_minute: int
    end_minute: int
    travel_seconds: float
    #: Minutes spent standing at the door because it was not open yet.
    wait_minutes: int
    #: Minutes of unstructured time before travelling to this stop.
    free_minutes: int = 0
    #: True when the time was chosen against a category default window because
    #: the place has no hours data. Surfaced in the UI as "hours unconfirmed".
    hours_assumed: bool = False

    @property
    def start_time(self) -> time:
        return minutes_to_time(self.start_minute)

    @property
    def end_time(self) -> time:
        return minutes_to_time(self.end_minute)

    @property
    def crosses_midnight(self) -> bool:
        return self.end_minute >= 24 * 60


@dataclass(slots=True)
class DayPlan:
    day_index: int
    day_date: date
    weekday: int
    stops: list[ScheduledStop] = field(default_factory=list)
    #: (stop, human-readable reason) for candidates that could not be placed.
    dropped: list[tuple[Stop, str]] = field(default_factory=list)
    total_travel_seconds: float = 0.0

    @property
    def total_dwell_minutes(self) -> int:
        return sum(s.end_minute - s.start_minute for s in self.stops)

    @property
    def total_wait_minutes(self) -> int:
        return sum(s.wait_minutes for s in self.stops)

    @property
    def total_free_minutes(self) -> int:
        return sum(s.free_minutes for s in self.stops)

    @property
    def score(self) -> float:
        return sum(s.stop.score for s in self.stops)


# --- feasibility ------------------------------------------------------------


def effective_hours(stop: Stop) -> tuple[dict[str, Any] | None, bool]:
    """Hours to schedule against, plus whether they were assumed.

    Returns the place's real hours when it has them. Otherwise falls back to the
    category default window, which is a prior about the category rather than a
    fact about the place — hence the flag.
    """
    if stop.hours:
        return stop.hours, False
    default = CATEGORY_DEFAULT_WINDOWS.get(stop.category)
    if default is None:
        return None, False
    return all_week(default), True


def _window_fits(stop: Stop, weekday: int, start: int) -> bool:
    """Whether the full dwell fits inside a single open interval.

    `earliest_open` only guarantees the place is open *at* `start`; a museum
    closing at 20:00 is open at 19:45 but cannot absorb a 105-minute visit.
    """
    hours, _ = effective_hours(stop)
    return is_open_during(hours, weekday, start, start + stop.dwell)


def _closing_minute(stop: Stop, weekday: int, start: int) -> int | None:
    """End of the open interval containing `start`, for urgency ordering."""
    hours, _ = effective_hours(stop)
    intervals = intervals_for(hours, weekday)
    if not intervals:
        return None
    for open_min, close_min in intervals:
        if open_min <= start < close_min:
            return close_min
    return None


# --- construction -----------------------------------------------------------


def simulate(
    order: list[int],
    stops: list[Stop],
    matrix: list[list[float]],
    weekday: int,
    day_start: int,
    day_end: int,
    max_leg_minutes: int = DEFAULT_MAX_LEG_MINUTES,
) -> tuple[list[ScheduledStop], list[tuple[Stop, str]]]:
    """Walk a fixed order, assigning times and dropping what does not fit.

    The single definition of a valid day: opening hours, the day window, daily
    category caps and same-category spacing are all enforced here.
    """
    scheduled: list[ScheduledStop] = []
    dropped: list[tuple[Stop, str]] = []
    clock = day_start
    previous: int | None = None
    counts: dict[PlaceCategory, int] = {}
    last_start: dict[PlaceCategory, int] = {}

    for index in order:
        stop = stops[index]
        limit = CATEGORY_DAILY_LIMITS.get(stop.category)
        if limit is not None and counts.get(stop.category, 0) >= limit:
            dropped.append((stop, f"already at the daily limit of {limit} {stop.category.value}"))
            continue

        if any(
            haversine_m(stop.lat, stop.lng, s.stop.lat, s.stop.lng) < MIN_STOP_SEPARATION_M
            for s in scheduled
        ):
            dropped.append(
                (stop, f"within {MIN_STOP_SEPARATION_M:.0f}m of a stop already scheduled today")
            )
            continue

        travel = 0.0 if previous is None else matrix[previous][index]
        leg_minutes = travel / 60.0
        if previous is not None and leg_minutes > max_leg_minutes:
            dropped.append(
                (stop, f"{leg_minutes:.0f}-minute leg exceeds the {max_leg_minutes}-minute "
                       f"maximum for a single hop")
            )
            continue
        arrival = clock + int(round(travel / 60.0))

        hours, assumed = effective_hours(stop)
        start = earliest_open(hours, weekday, arrival)
        if start is None:
            dropped.append((stop, f"closed for the rest of the day by {minutes_to_time(arrival)}"))
            continue

        gap = MIN_SAME_CATEGORY_GAP_MINUTES.get(stop.category)
        if gap is not None and stop.category in last_start:
            required = last_start[stop.category] + gap
            if start < required:
                deferred = earliest_open(hours, weekday, required)
                if deferred is None:
                    dropped.append(
                        (stop, f"another {stop.category.value} was too recent and it does not "
                               f"reopen before the day ends")
                    )
                    continue
                start = deferred
        if not _window_fits(stop, weekday, start):
            dropped.append(
                (stop, f"open window too short for a {stop.dwell}-minute visit at "
                       f"{minutes_to_time(start)}")
            )
            continue
        end = start + stop.dwell
        if end > day_end:
            dropped.append((stop, f"would end at {minutes_to_time(end)}, past the day's window"))
            continue

        counts[stop.category] = counts.get(stop.category, 0) + 1
        last_start[stop.category] = start

        idle = max(0, start - arrival)
        if idle > FREE_TIME_THRESHOLD_MINUTES:
            # Defer the travel: you spend the gap elsewhere and arrive shortly
            # before opening, rather than standing outside for five hours.
            free_minutes = idle
            wait_minutes = 0
            arrival = start
        else:
            free_minutes = 0
            wait_minutes = idle

        scheduled.append(
            ScheduledStop(
                stop=stop,
                arrival_minute=arrival,
                start_minute=start,
                end_minute=end,
                travel_seconds=travel,
                wait_minutes=wait_minutes,
                free_minutes=free_minutes,
                hours_assumed=assumed,
            )
        )
        clock = end
        previous = index

    return scheduled, dropped


def build_day_order(
    stops: list[Stop],
    matrix: list[list[float]],
    weekday: int,
    day_start: int,
    day_end: int,
    max_leg_minutes: int = DEFAULT_MAX_LEG_MINUTES,
) -> list[int]:
    """Time-aware greedy construction.

    At each step, every unvisited stop is evaluated for whether it is *actually
    open* when we could arrive, and the cheapest feasible one by
    (travel + penalised wait + category-repetition penalty) is taken.
    """
    remaining = set(range(len(stops)))
    order: list[int] = []
    clock = day_start
    previous: int | None = None
    counts: dict[PlaceCategory, int] = {}
    last_start: dict[PlaceCategory, int] = {}

    while remaining:
        best: tuple[float, int, int] | None = None  # cost, closing urgency, index
        for index in remaining:
            stop = stops[index]
            limit = CATEGORY_DAILY_LIMITS.get(stop.category)
            if limit is not None and counts.get(stop.category, 0) >= limit:
                continue

            if any(
                haversine_m(stop.lat, stop.lng, stops[c].lat, stops[c].lng)
                < MIN_STOP_SEPARATION_M
                for c in order
            ):
                continue
            travel = 0.0 if previous is None else matrix[previous][index]
            if previous is not None and travel / 60.0 > max_leg_minutes:
                continue
            arrival = clock + int(round(travel / 60.0))
            hours, _ = effective_hours(stop)
            start = earliest_open(hours, weekday, arrival)
            if start is None:
                continue
            gap = MIN_SAME_CATEGORY_GAP_MINUTES.get(stop.category)
            if gap is not None and stop.category in last_start:
                required = last_start[stop.category] + gap
                if start < required:
                    start = earliest_open(hours, weekday, required)
                    if start is None:
                        continue
            if not _window_fits(stop, weekday, start):
                continue
            if start + stop.dwell > day_end:
                continue

            idle = max(0, start - arrival)
            if idle > FREE_TIME_THRESHOLD_MINUTES:
                cost = travel + FREE_TIME_PENALTY * idle * 60.0
            else:
                cost = travel + WAIT_PENALTY * idle * 60.0
            if previous is not None and stops[previous].category == stop.category:
                cost += SAME_CATEGORY_PENALTY_SECONDS
            # Quality has to weigh heavily here, or the greedy fills a day with
            # whatever is nearest: a 5-minute-equivalent bonus let a cinema
            # (score 55) beat the Prado (score 75) and left the city's best
            # museums unscheduled. Scaled so a 20-point score gap is worth
            # about 10 minutes of walking.
            cost -= stop.score * SCORE_TO_SECONDS
            # Strongly prefer eating at mealtimes. Without this the greedy fills
            # the middle of the day with sights and never schedules lunch.
            if stop.category in MEAL_CATEGORIES and any(
                lo - MEAL_BONUS_LEAD_MINUTES <= arrival <= hi for lo, hi in MEAL_WINDOWS
            ):
                cost -= MEAL_WINDOW_BONUS_SECONDS

            # Tie-break toward stops whose open window closes soonest: a place
            # shutting in 40 minutes must be taken now or lost.
            closing = _closing_minute(stop, weekday, start) or 10**6
            if best is None or (cost, closing) < (best[0], best[1]):
                best = (cost, closing, index)

        if best is None:
            break

        _, _, index = best
        stop = stops[index]
        travel = 0.0 if previous is None else matrix[previous][index]
        arrival = clock + int(round(travel / 60.0))
        hours, _ = effective_hours(stop)
        start = earliest_open(hours, weekday, arrival) or arrival
        gap = MIN_SAME_CATEGORY_GAP_MINUTES.get(stop.category)
        if gap is not None and stop.category in last_start:
            required = last_start[stop.category] + gap
            if start < required:
                start = earliest_open(hours, weekday, required) or required
        clock = start + stop.dwell
        counts[stop.category] = counts.get(stop.category, 0) + 1
        last_start[stop.category] = start
        order.append(index)
        remaining.discard(index)
        previous = index

    return order


def schedule_day(
    day_index: int,
    day_date: date,
    candidate_stops: list[Stop],
    provider: TravelTimeProvider,
    *,
    day_start_hour: int,
    day_end_hour: int,
    max_stops: int,
    max_leg_minutes: int = DEFAULT_MAX_LEG_MINUTES,
) -> DayPlan:
    """Produce one day's ordered, timed itinerary."""
    weekday = day_date.weekday()
    day_start = day_start_hour * 60
    day_end = day_end_hour * 60
    plan = DayPlan(day_index=day_index, day_date=day_date, weekday=weekday)

    if not candidate_stops:
        return plan

    matrix = provider.matrix([s.point for s in candidate_stops])
    order = build_day_order(
        candidate_stops, matrix, weekday, day_start, day_end, max_leg_minutes
    )

    if not order:
        # Re-run the simulation over every candidate so the caller gets the
        # specific reason per stop ("open window too short", "closed for the
        # rest of the day") instead of a useless blanket message.
        _, reasons = simulate(
            list(range(len(candidate_stops))),
            candidate_stops,
            matrix,
            weekday,
            day_start,
            day_end,
            max_leg_minutes,
        )
        seen = {stop.key for stop, _ in reasons}
        plan.dropped = list(reasons) + [
            (s, "nothing could be scheduled inside the day's window")
            for s in candidate_stops
            if s.key not in seen
        ]
        return plan

    order = order[:max_stops]

    # 2-opt over the chosen stops. Both hooks matter: `feasible` stops a
    # reordering from silently dropping a stop, and `cost` charges for waiting
    # so the pass cannot trade hours of idle time for minutes of walking.
    if len(order) >= 4:
        from app.optimizer.route import two_opt

        target = len(order)

        def feasible(candidate: list[int]) -> bool:
            kept, _ = simulate(
                candidate, candidate_stops, matrix, weekday, day_start, day_end, max_leg_minutes
            )
            return len(kept) == target

        def cost(candidate: list[int]) -> float:
            kept, _ = simulate(
                candidate, candidate_stops, matrix, weekday, day_start, day_end, max_leg_minutes
            )
            travel = sum(s.travel_seconds for s in kept)
            waiting = sum(s.wait_minutes for s in kept) * 60.0
            free = sum(s.free_minutes for s in kept) * 60.0
            return travel + WAIT_PENALTY * waiting + FREE_TIME_PENALTY * free

        order = two_opt(order, matrix, is_feasible=feasible, cost_fn=cost)

    scheduled, dropped = simulate(
        order, candidate_stops, matrix, weekday, day_start, day_end, max_leg_minutes
    )
    plan.stops = scheduled
    plan.total_travel_seconds = sum(s.travel_seconds for s in scheduled)

    placed = {s.stop.key for s in scheduled}
    plan.dropped = list(dropped) + [
        (s, "not selected for this day")
        for s in candidate_stops
        if s.key not in placed and all(s.key != d[0].key for d in dropped)
    ]
    return plan
