"""Database integration for scoring.

Kept separate from `model.py` so the scoring logic stays a pure, testable
function of its inputs. This module's only jobs are: measure each source's
rating distribution from the data actually present, feed signals through the
model, and persist the score plus its full breakdown.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Place, SourceName, SourceSignal
from app.scoring.model import (
    ScoreResult,
    SignalInput,
    SourceDistribution,
    distribution_for,
    score_place,
)

log = logging.getLogger(__name__)


def measure_distributions(session: Session, city: str) -> dict[SourceName, SourceDistribution]:
    """Measure each source's rating distribution over this city's places.

    Measured per city rather than globally: rating inflation is regional. A
    source's Madrid mean is the right reference for a Madrid place, and using a
    worldwide mean would systematically mis-centre every z-score.
    """
    rows = session.execute(
        select(SourceSignal.source, SourceSignal.rating)
        .join(Place, Place.id == SourceSignal.place_id)
        .where(
            Place.city == city,
            Place.duplicate_of.is_(None),
            SourceSignal.rating.isnot(None),
        )
    ).all()

    by_source: dict[SourceName, list[float]] = {}
    for source, rating in rows:
        by_source.setdefault(source, []).append(float(rating))

    distributions = {
        source: distribution_for(source, ratings) for source, ratings in by_source.items()
    }
    for source, dist in sorted(distributions.items(), key=lambda kv: kv[0].value):
        log.info(
            "%s rating distribution: mean=%.3f sd=%.3f n=%d (%s)",
            source.value,
            dist.mean,
            dist.std,
            dist.sample_size,
            "measured" if dist.measured else "fallback prior",
        )
    return distributions


def to_signal_inputs(place: Place) -> list[SignalInput]:
    return [
        SignalInput(
            source=s.source,
            rating=float(s.rating) if s.rating is not None else None,
            review_count=s.review_count,
            observed_at=s.observed_at,
            extra=dict(s.extra or {}),
        )
        for s in place.signals
    ]


def score_one(
    place: Place,
    distributions: dict[SourceName, SourceDistribution],
    now: datetime | None = None,
) -> ScoreResult:
    return score_place(to_signal_inputs(place), distributions, now)


def rescore_city(session: Session, city: str) -> int:
    """Recompute and persist composite scores for every canonical place."""
    now = datetime.now(UTC)
    distributions = measure_distributions(session, city)

    places = list(
        session.execute(
            select(Place)
            .options(selectinload(Place.signals))
            .where(Place.city == city, Place.duplicate_of.is_(None))
        ).scalars()
    )

    for place in places:
        result = score_one(place, distributions, now)
        place.composite_score = result.composite_score
        place.score_breakdown = result.as_dict()
        place.scored_at = now

    session.flush()
    log.info("scored %d places in %s", len(places), city)
    return len(places)
