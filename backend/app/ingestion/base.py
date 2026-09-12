"""The contract every ingestion source implements.

Each adapter's job is narrow: fetch from its source and emit `RawPlace`
objects. Adapters never touch the database. All persistence, deduplication and
merge logic lives in `app.ingestion.resolver`, which means a new source is
~100 lines of mapping code and inherits dedup for free.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config import CityConfig, Settings
from app.models.enums import IngestMode, PlaceCategory, SourceName

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RawEvidence:
    """A snippet of unstructured text attached to a place mention."""

    text: str
    url: str | None = None
    #: Relative importance of this snippet (Reddit: upvotes). Defaults to 1.0.
    weight: float = 1.0


@dataclass(slots=True)
class RawPlace:
    """Source-agnostic intermediate representation.

    Note `lat`/`lng` are optional. Reddit and blog mentions are bare names in
    prose with no coordinates; the resolver handles those by name-matching them
    onto places that a geocoded source already established, and dropping them
    otherwise (an unmappable, unroutable place is worse than no place).
    """

    source: SourceName
    source_place_id: str
    name: str
    city: str
    category: PlaceCategory = PlaceCategory.OTHER

    lat: float | None = None
    lng: float | None = None
    country_code: str | None = None
    address: str | None = None
    neighborhood: str | None = None
    price_tier: int | None = None
    hours: dict[str, Any] | None = None

    #: Normalized to a 0-5 scale by the adapter.
    rating: float | None = None
    #: The source's native value, preserved for debugging.
    raw_rating: float | None = None
    review_count: int | None = None
    url: str | None = None

    extra: dict[str, Any] = field(default_factory=dict)
    observed_at: datetime | None = None
    evidence: list[RawEvidence] = field(default_factory=list)

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lng is not None


@dataclass(slots=True)
class FetchResult:
    places: list[RawPlace]
    mode: IngestMode
    #: Human-readable note surfaced by the CLI, e.g. why we fell back.
    note: str = ""


class SourceAdapter(abc.ABC):
    """Base class for a single ingestion source.

    The live/fixture split is handled here rather than in each adapter so the
    fallback behaviour is uniform and impossible to forget: `fetch()` decides
    the mode, calls the right hook, and degrades to fixtures if a live call
    raises. Sources are therefore never a hard dependency of the pipeline.
    """

    source: SourceName
    #: If False, a live failure propagates instead of silently degrading.
    #: Every adapter in v1 is soft, per the spec's "scraper must not be a
    #: dependency for core functionality".
    soft_fail: bool = True

    def __init__(self, settings: Settings, city: CityConfig) -> None:
        self.settings = settings
        self.city = city

    # --- to implement -------------------------------------------------------

    @abc.abstractmethod
    def has_credentials(self) -> bool:
        """Whether a live fetch is even possible."""

    @abc.abstractmethod
    def fetch_live(self) -> list[RawPlace]:
        """Hit the real source."""

    @abc.abstractmethod
    def fetch_fixture(self) -> list[RawPlace]:
        """Read checked-in sample data shaped exactly like the live response."""

    # --- orchestration ------------------------------------------------------

    def fetch(self, *, force_fixtures: bool = False) -> FetchResult:
        use_fixtures = force_fixtures or self.settings.ingestion_force_fixtures
        if use_fixtures:
            return FetchResult(self.fetch_fixture(), IngestMode.FIXTURE, "forced by configuration")
        if not self.has_credentials():
            return FetchResult(
                self.fetch_fixture(), IngestMode.FIXTURE, "no credentials configured"
            )
        try:
            return FetchResult(self.fetch_live(), IngestMode.LIVE, "")
        except Exception as exc:  # noqa: BLE001 - deliberate degradation boundary
            if not self.soft_fail:
                raise
            log.warning("%s live fetch failed (%s); falling back to fixtures", self.source, exc)
            return FetchResult(
                self.fetch_fixture(), IngestMode.FIXTURE, f"live fetch failed: {exc}"
            )
