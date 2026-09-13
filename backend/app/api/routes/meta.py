"""Health and provenance endpoints.

`/meta/provenance` exists so the "what is mocked vs real" question is answerable
from the running app rather than from the README — it reports each ingestion
run's mode straight out of the `ingest_runs` audit table.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import IngestRun, Place, SourceSignal

router = APIRouter(tags=["meta"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(select(1))
    return {"status": "ok"}


@router.get("/meta/provenance")
def provenance(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    city = settings.city.name

    latest = (
        select(
            IngestRun.source,
            func.max(IngestRun.started_at).label("started_at"),
        )
        .where(IngestRun.city == city)
        .group_by(IngestRun.source)
        .subquery()
    )
    runs = db.execute(
        select(IngestRun)
        .join(
            latest,
            (IngestRun.source == latest.c.source)
            & (IngestRun.started_at == latest.c.started_at),
        )
        .order_by(IngestRun.source)
    ).scalars()

    signal_counts = dict(
        db.execute(
            select(SourceSignal.source, func.count())
            .join(Place, Place.id == SourceSignal.place_id)
            .where(Place.city == city)
            .group_by(SourceSignal.source)
        ).all()
    )

    return {
        "city": city,
        "routing_provider": settings.routing_provider,
        "travel_mode": settings.travel_mode,
        "sources": [
            {
                "source": run.source.value,
                "mode": run.mode,
                "is_live": run.mode == "live",
                "records_fetched": run.records_fetched,
                "places_created": run.places_created,
                "places_merged": run.places_merged,
                "flagged_for_review": run.flagged_for_review,
                "signals_in_db": signal_counts.get(run.source, 0),
                "last_run": run.started_at,
                "error": run.error,
            }
            for run in runs
        ],
    }
