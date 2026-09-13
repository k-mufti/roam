"""Candidate pool selection.

Turns a request (tags, budget, pace, radius) into a ranked pool of `Stop`s for
the clusterer. Two decisions worth naming:

**Tags are preferences, not filters.** The spec says "a user's selected/preferred
tags". Hard-filtering on them empties the pool fast — asking for `quiet` AND
`budget` AND `family-friendly` in a city where tags come from review text means
most good places are excluded for lack of evidence rather than for being wrong.
So matched tags *boost* a place's ranking, and unmatched places remain eligible
at their own merit.

**Budget and radius are real filters**, because they are hard constraints on the
traveller rather than tastes. A place above the budget tier is genuinely not an
option. Places with an unknown price tier are kept — absence of data is not
evidence of expense.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import LODGING_CATEGORIES, Place, PlaceCategory
from app.optimizer.schedule import Stop

log = logging.getLogger(__name__)

#: Score multiplier at full tag match. A place matching every requested tag
#: ranks as if it scored 35% higher. Enough to reorder the pool, not enough to
#: drag a mediocre place above an excellent one.
TAG_BOOST = 0.35

#: Tags that disqualify a place when the traveller has asked to avoid them.
DEFAULT_AVOID_TAGS: tuple[str, ...] = ()

#: Pool size relative to the number of stops actually needed. Oversampling gives
#: the clusterer room to form compact days and the scheduler room to substitute
#: when its first choice is closed.
POOL_OVERSAMPLE = 4
MIN_POOL_SIZE = 24


@dataclass(frozen=True, slots=True)
class CandidateRequest:
    city: str
    preferred_tags: tuple[str, ...] = ()
    avoid_tags: tuple[str, ...] = DEFAULT_AVOID_TAGS
    max_price_tier: int = 4
    #: Radius filter around a point, in metres. The spec's "3-5 mile" view.
    center: tuple[float, float] | None = None
    radius_m: float | None = None
    #: Categories to exclude entirely; lodging is excluded by default because
    #: you sleep at a hotel, you do not schedule it as a stop.
    exclude_categories: frozenset[PlaceCategory] = LODGING_CATEGORIES
    limit: int = 60


@dataclass(slots=True)
class ScoredCandidate:
    stop: Stop
    base_score: float
    tag_matches: tuple[str, ...]
    boosted_score: float


def select_candidates(session: Session, request: CandidateRequest) -> list[ScoredCandidate]:
    """Fetch and rank candidate stops."""
    stmt = select(Place).where(
        Place.city == request.city,
        Place.duplicate_of.is_(None),
        Place.composite_score.isnot(None),
    )

    if request.exclude_categories:
        stmt = stmt.where(Place.category.notin_(list(request.exclude_categories)))

    # NULL price tier is kept: unknown price is not the same as unaffordable.
    if request.max_price_tier < 4:
        stmt = stmt.where(
            (Place.price_tier.is_(None)) | (Place.price_tier <= request.max_price_tier)
        )

    if request.avoid_tags:
        stmt = stmt.where(~Place.tags.overlap(list(request.avoid_tags)))

    if request.center and request.radius_m:
        lat, lng = request.center
        point = func.ST_GeogFromText(f"SRID=4326;POINT({lng} {lat})")
        # PostGIS does the radius filter on the geography column, using metres
        # directly and the GiST index.
        stmt = stmt.where(func.ST_DWithin(Place.geom, point, request.radius_m))

    stmt = stmt.order_by(Place.composite_score.desc()).limit(request.limit)
    places = list(session.execute(stmt).scalars())

    preferred = {t.lower() for t in request.preferred_tags}
    out: list[ScoredCandidate] = []
    for place in places:
        tags = tuple(place.tags or ())
        matches = tuple(t for t in tags if t.lower() in preferred)
        fraction = len(matches) / len(preferred) if preferred else 0.0
        base = float(place.composite_score or 0.0)
        out.append(
            ScoredCandidate(
                stop=Stop(
                    key=str(place.id),
                    name=place.name,
                    lat=place.lat,
                    lng=place.lng,
                    category=place.category,
                    score=base,
                    price_tier=place.price_tier,
                    tags=tags,
                    hours=place.hours,
                    address=place.address,
                ),
                base_score=base,
                tag_matches=matches,
                boosted_score=base * (1.0 + TAG_BOOST * fraction),
            )
        )

    out.sort(key=lambda c: -c.boosted_score)
    log.info(
        "candidate pool: %d places for %s (tags=%s, max_price=%d)",
        len(out),
        request.city,
        ",".join(request.preferred_tags) or "-",
        request.max_price_tier,
    )
    return out


def pool_size_for(days: int, stops_per_day: int) -> int:
    return max(MIN_POOL_SIZE, days * stops_per_day * POOL_OVERSAMPLE)
