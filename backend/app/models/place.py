"""SQLAlchemy models.

Schema design notes (the interesting decisions):

* `geom` is `geography(Point, 4326)`, not `geometry`. Geography makes
  `ST_DWithin(geom, point, 5000)` take **meters** directly and stay correct
  across projections, which is exactly the "places within 3-5 miles" query.
  With `geometry(4326)` the same call would compare degrees and silently give
  wrong answers at Madrid's latitude. Cost: geography ops are slower, but at
  a few thousand rows per city that is irrelevant.

* `lat`/`lng` are stored as plain columns *in addition to* `geom`. Redundant,
  but it means the API can serialize coordinates without a PostGIS round-trip
  and the fixtures/tests stay readable. `Place.set_location()` is the single
  writer that keeps them in sync.

* `source_signals` is a child table, not a JSONB array. The spec describes it
  as an array, and it is exposed that way in the API, but relationally it needs
  a uniqueness constraint on `(source, source_place_id)` for idempotent
  re-ingestion — that is not expressible inside a JSONB blob.

* `hours` *is* JSONB: it is read as a whole, never queried by key, and its
  shape (multiple open intervals per day) is awkward in columns.

* `tags` is a Postgres `TEXT[]` with a GIN index so tag filtering is
  `tags && ARRAY['quiet','solo']` rather than a join.

* `duplicate_of` is a nullable self-FK. Losers of a merge are *kept*, not
  deleted, so entity resolution is auditable and reversible.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.models.enums import MergeDecision, PlaceCategory, SourceName


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


TS = DateTime(timezone=True)


def _pg_enum(enum_cls: type, name: str, *, create_type: bool = True) -> SAEnum:
    """Postgres enum column bound to a Python StrEnum.

    `values_callable` is not optional here: without it SQLAlchemy persists the
    member *name* ("GOOGLE_PLACES") while the database type holds the member
    *value* ("google_places"). `create_type=False` is passed for types shared
    across tables so only the first definition emits CREATE TYPE.
    """
    return SAEnum(
        enum_cls,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        create_type=create_type,
    )


class Place(Base):
    """One physical place, deduplicated across all sources."""

    __tablename__ = "places"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=_uuid)

    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: Casefolded, accent-stripped, noise-word-free form of `name`. Persisted
    #: (not computed at query time) so the pg_trgm GIN index can serve the
    #: candidate-blocking step of entity resolution.
    name_normalized: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    city: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    country_code: Mapped[str | None] = mapped_column(String(2))
    address: Mapped[str | None] = mapped_column(Text)
    neighborhood: Mapped[str | None] = mapped_column(String(160))

    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    geom: Mapped[Any] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False), nullable=False
    )

    category: Mapped[PlaceCategory] = mapped_column(
        _pg_enum(PlaceCategory, "place_category"), nullable=False, default=PlaceCategory.OTHER
    )
    price_tier: Mapped[int | None] = mapped_column(Integer)

    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)

    #: {"mon": [{"open": "09:00", "close": "17:00"}], ..., "sun": []}
    #: An empty list means "closed that day"; a missing key means "unknown",
    #: which the optimizer treats as permissive rather than as closed.
    hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    composite_score: Mapped[float | None] = mapped_column(Float, index=True)
    #: Full audit trail from the scoring module: per-source contributions,
    #: weights applied, and corroboration bonus. Persisted so the API can
    #: explain a ranking without recomputing it.
    score_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    scored_at: Mapped[datetime | None] = mapped_column(TS)

    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("places.id", ondelete="SET NULL"), index=True
    )
    #: Similarity score of the merge that retired this record, for auditing.
    merge_confidence: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TS, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    signals: Mapped[list[SourceSignal]] = relationship(
        back_populates="place", cascade="all, delete-orphan", lazy="selectin"
    )
    evidence: Mapped[list[TextEvidence]] = relationship(
        back_populates="place", cascade="all, delete-orphan", lazy="select"
    )
    canonical: Mapped[Place | None] = relationship(remote_side=[id], lazy="select")

    __table_args__ = (
        CheckConstraint("price_tier IS NULL OR price_tier BETWEEN 1 AND 4", name="ck_price_tier"),
        CheckConstraint("lat BETWEEN -90 AND 90", name="ck_lat_range"),
        CheckConstraint("lng BETWEEN -180 AND 180", name="ck_lng_range"),
        CheckConstraint("duplicate_of IS NULL OR duplicate_of <> id", name="ck_no_self_duplicate"),
        Index("ix_places_geom", "geom", postgresql_using="gist"),
        Index("ix_places_tags", "tags", postgresql_using="gin"),
        Index(
            "ix_places_name_trgm",
            "name_normalized",
            postgresql_using="gin",
            postgresql_ops={"name_normalized": "gin_trgm_ops"},
        ),
        # The hot path: "top-scoring non-duplicate places in this city".
        Index(
            "ix_places_city_active_score",
            "city",
            "composite_score",
            postgresql_where=(duplicate_of.is_(None)),
        ),
    )

    @property
    def is_canonical(self) -> bool:
        return self.duplicate_of is None

    def set_location(self, lat: float, lng: float) -> None:
        """Only writer of coordinates: keeps `lat`/`lng`/`geom` consistent."""
        self.lat = lat
        self.lng = lng
        self.geom = f"SRID=4326;POINT({lng} {lat})"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Place {self.name!r} {self.category} score={self.composite_score}>"


class SourceSignal(Base):
    """One source's opinion about one place.

    Ratings are stored twice on purpose: `raw_rating` preserves the source's
    native scale (Yelp 1-5, Google 1-5, Reddit has none) for debugging, while
    `rating` is normalized to 0-5 so the scoring module never has to know which
    source it is looking at.
    """

    __tablename__ = "source_signals"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=_uuid)
    place_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("places.id", ondelete="CASCADE"), nullable=False
    )

    source: Mapped[SourceName] = mapped_column(_pg_enum(SourceName, "source_name"), nullable=False)
    #: Stable per-source identifier. For Reddit/blog mentions there is no such
    #: id, so adapters synthesize a deterministic one (e.g. a hash of the
    #: thread id + matched name) to keep re-ingestion idempotent.
    source_place_id: Mapped[str] = mapped_column(String(256), nullable=False)

    rating: Mapped[float | None] = mapped_column(Float)
    raw_rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(Text)

    #: Source-specific extras that don't deserve columns. Reddit puts
    #: {thread_score, num_comments, mention_count, sentiment, subreddit} here;
    #: the scoring module reads those by key.
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    #: When the *source data* was last produced (Google's review recency, a
    #: Reddit thread's creation date). Drives the recency decay in scoring.
    observed_at: Mapped[datetime | None] = mapped_column(TS)
    #: When *we* fetched it.
    last_updated: Mapped[datetime] = mapped_column(TS, server_default=func.now(), nullable=False)

    place: Mapped[Place] = relationship(back_populates="signals")

    __table_args__ = (
        UniqueConstraint("source", "source_place_id", name="uq_signal_source_identity"),
        CheckConstraint("rating IS NULL OR rating BETWEEN 0 AND 5", name="ck_rating_range"),
        CheckConstraint("review_count IS NULL OR review_count >= 0", name="ck_review_count"),
        Index("ix_signals_place_source", "place_id", "source"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SourceSignal {self.source} rating={self.rating} n={self.review_count}>"


class TextEvidence(Base):
    """A snippet of unstructured text about a place, with provenance.

    Kept as first-class rows rather than buried in `SourceSignal.extra` because
    the tagging pipeline needs to iterate over *all* text for a place, and
    because being able to show "we tagged this 'quiet' because of these three
    comments" is most of the demo value.
    """

    __tablename__ = "text_evidence"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=_uuid)
    place_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("places.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[SourceName] = mapped_column(
        _pg_enum(SourceName, "source_name", create_type=False), nullable=False
    )
    #: Deduplicating hash of the snippet text, so re-ingesting a thread does
    #: not multiply the evidence.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    #: VADER compound score, -1..1.
    sentiment: Mapped[float | None] = mapped_column(Float)
    #: Upvotes for Reddit comments; None elsewhere. Used to weight the snippet.
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now(), nullable=False)

    place: Mapped[Place] = relationship(back_populates="evidence")

    __table_args__ = (
        UniqueConstraint("place_id", "content_hash", name="uq_evidence_place_content"),
        Index("ix_evidence_place", "place_id"),
    )


class MergeReview(Base):
    """An entity-resolution decision that was *not* confident enough to apply.

    The spec asks for ambiguous merges to be flagged rather than guessed. This
    table is that queue: nothing reads it at runtime, and `trip resolve
    --review` prints it.
    """

    __tablename__ = "merge_reviews"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=_uuid)
    left_place_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("places.id", ondelete="CASCADE"), nullable=False
    )
    right_place_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("places.id", ondelete="CASCADE"), nullable=False
    )
    decision: Mapped[MergeDecision] = mapped_column(
        _pg_enum(MergeDecision, "merge_decision"), nullable=False
    )
    name_similarity: Mapped[float] = mapped_column(Float, nullable=False)
    distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("left_place_id", "right_place_id", name="uq_merge_pair"),
    )


class IngestRun(Base):
    """Audit row per ingestion invocation — what ran, live or fixture, and what
    it produced. Makes "what's mocked vs real" answerable from the database
    instead of from memory."""

    __tablename__ = "ingest_runs"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=_uuid)
    source: Mapped[SourceName] = mapped_column(
        _pg_enum(SourceName, "source_name", create_type=False), nullable=False
    )
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    records_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    places_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    places_merged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flagged_for_review: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(TS, server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
