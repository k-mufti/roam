"""Ingestion orchestration.

Each source is independently runnable (`roam ingest --source yelp`) and all of
them share one write path through `EntityResolver`, so dedup behaviour cannot
drift between sources. Every run writes an `IngestRun` audit row recording
whether it was live or fixture-backed — that is how the README's "what's mocked
vs real" claim stays verifiable instead of aspirational.

Ordering matters and is enforced by `SOURCE_ORDER`: geocoded sources must run
before coordinate-less ones, because a Reddit mention can only attach to a
place that already exists with coordinates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import CityConfig, Settings
from app.ingestion.base import SourceAdapter
from app.ingestion.resolver import EntityResolver, ResolutionStats
from app.models import IngestRun, SourceName

log = logging.getLogger(__name__)

#: Geocoded, authoritative sources first; name-only mention sources last.
SOURCE_ORDER: tuple[SourceName, ...] = (
    SourceName.GOOGLE_PLACES,
    SourceName.YELP,
    SourceName.BLOG,
    SourceName.REDDIT,
)


def build_adapter(
    source: SourceName, settings: Settings, city: CityConfig, *, refresh: bool = False
) -> SourceAdapter:
    """Adapter factory. Imports are local so a broken optional dependency in one
    source cannot stop the others from running."""
    if source is SourceName.GOOGLE_PLACES:
        from app.ingestion.google_places import GooglePlacesAdapter

        return GooglePlacesAdapter(settings, city, refresh=refresh)
    if source is SourceName.YELP:
        from app.ingestion.yelp import YelpAdapter

        return YelpAdapter(settings, city, refresh=refresh)
    if source is SourceName.REDDIT:
        from app.ingestion.reddit import RedditAdapter

        return RedditAdapter(settings, city, refresh=refresh)
    if source is SourceName.BLOG:
        from app.ingestion.scraper import BlogScraperAdapter

        return BlogScraperAdapter(settings, city, refresh=refresh)
    raise ValueError(f"unknown source: {source}")


@dataclass(slots=True)
class RunReport:
    source: SourceName
    mode: str
    note: str
    fetched: int
    stats: ResolutionStats
    error: str | None = None

    def summary(self) -> str:
        s = self.stats
        head = f"{self.source:<15} {self.mode:<8} fetched={self.fetched:<4}"
        body = (
            f"created={s.created} merged={s.merged} updated={s.updated} "
            f"flagged={s.flagged} dropped={s.dropped}"
        )
        tail = f"  ({self.note})" if self.note else ""
        return head + body + tail + (f"  ERROR: {self.error}" if self.error else "")


def run_source(
    session: Session,
    source: SourceName,
    settings: Settings,
    city: CityConfig,
    *,
    force_fixtures: bool = False,
    refresh: bool = False,
) -> RunReport:
    run = IngestRun(source=source, city=city.name, mode="pending")
    session.add(run)
    session.flush()

    try:
        adapter = build_adapter(source, settings, city, refresh=refresh)
        result = adapter.fetch(force_fixtures=force_fixtures)
        resolver = EntityResolver(session)
        stats = resolver.ingest_all(result.places)

        run.mode = result.mode.value
        run.records_fetched = len(result.places)
        run.places_created = stats.created
        run.places_merged = stats.merged
        run.flagged_for_review = stats.flagged
        run.finished_at = datetime.now(UTC)
        session.flush()
        return RunReport(source, result.mode.value, result.note, len(result.places), stats)
    except Exception as exc:  # noqa: BLE001 - report, don't abort the whole run
        log.exception("ingestion failed for %s", source)
        run.mode = "failed"
        run.error = str(exc)[:2000]
        run.finished_at = datetime.now(UTC)
        session.flush()
        return RunReport(source, "failed", "", 0, ResolutionStats(), error=str(exc))


def run_all(
    session: Session,
    settings: Settings,
    city: CityConfig,
    *,
    sources: tuple[SourceName, ...] | None = None,
    force_fixtures: bool = False,
    refresh: bool = False,
) -> list[RunReport]:
    selected = sources or SOURCE_ORDER
    ordered = [s for s in SOURCE_ORDER if s in selected]
    return [
        run_source(
            session, s, settings, city, force_fixtures=force_fixtures, refresh=refresh
        )
        for s in ordered
    ]
