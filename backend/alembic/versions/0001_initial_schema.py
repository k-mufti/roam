"""Initial schema: places, source_signals, text_evidence, merge_reviews, ingest_runs.

Revision ID: 0001
Revises:
Create Date: 2026-09-12
"""

from __future__ import annotations

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


# create_type=False: these are created explicitly (and idempotently) in
# upgrade() below. Without it, SQLAlchemy also emits a CREATE TYPE from inside
# each create_table that references the enum, which fails the second time a
# shared type like `source_name` is used.
place_category = postgresql.ENUM(
    "restaurant", "cafe", "bar", "attraction", "museum", "park",
    "shopping", "hotel", "nightlife", "other",
    name="place_category",
    create_type=False,
)
source_name = postgresql.ENUM(
    "google_places", "yelp", "reddit", "blog", name="source_name", create_type=False
)
merge_decision = postgresql.ENUM(
    "merged", "created", "flagged", name="merge_decision", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()

    # postgis: geography/GiST. pg_trgm: trigram similarity index used to block
    # entity-resolution candidates by name before the expensive fuzzy compare.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    place_category.create(bind, checkfirst=True)
    source_name.create(bind, checkfirst=True)
    merge_decision.create(bind, checkfirst=True)

    op.create_table(
        "places",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_normalized", sa.Text(), nullable=False),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("country_code", sa.String(length=2)),
        sa.Column("address", sa.Text()),
        sa.Column("neighborhood", sa.String(length=160)),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column(
            "geom",
            geoalchemy2.types.Geography(
                geometry_type="POINT", srid=4326, spatial_index=False, from_text="ST_GeogFromText"
            ),
            nullable=False,
        ),
        sa.Column("category", place_category, nullable=False, server_default="other"),
        sa.Column("price_tier", sa.Integer()),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("hours", postgresql.JSONB()),
        sa.Column("composite_score", sa.Float()),
        sa.Column("score_breakdown", postgresql.JSONB()),
        sa.Column("scored_at", sa.DateTime(timezone=True)),
        sa.Column("duplicate_of", postgresql.UUID(as_uuid=True)),
        sa.Column("merge_confidence", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["duplicate_of"], ["places.id"], ondelete="SET NULL"),
        sa.CheckConstraint("price_tier IS NULL OR price_tier BETWEEN 1 AND 4", name="ck_price_tier"),
        sa.CheckConstraint("lat BETWEEN -90 AND 90", name="ck_lat_range"),
        sa.CheckConstraint("lng BETWEEN -180 AND 180", name="ck_lng_range"),
        sa.CheckConstraint("duplicate_of IS NULL OR duplicate_of <> id", name="ck_no_self_duplicate"),
    )
    op.create_index("ix_places_city", "places", ["city"])
    op.create_index("ix_places_name_normalized", "places", ["name_normalized"])
    op.create_index("ix_places_composite_score", "places", ["composite_score"])
    op.create_index("ix_places_duplicate_of", "places", ["duplicate_of"])
    op.create_index("ix_places_geom", "places", ["geom"], postgresql_using="gist")
    op.create_index("ix_places_tags", "places", ["tags"], postgresql_using="gin")
    op.create_index(
        "ix_places_name_trgm",
        "places",
        ["name_normalized"],
        postgresql_using="gin",
        postgresql_ops={"name_normalized": "gin_trgm_ops"},
    )
    # Partial index: every user-facing query filters duplicate_of IS NULL.
    op.create_index(
        "ix_places_city_active_score",
        "places",
        ["city", "composite_score"],
        postgresql_where=sa.text("duplicate_of IS NULL"),
    )

    op.create_table(
        "source_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("place_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", source_name, nullable=False),
        sa.Column("source_place_id", sa.String(length=256), nullable=False),
        sa.Column("rating", sa.Float()),
        sa.Column("raw_rating", sa.Float()),
        sa.Column("review_count", sa.Integer()),
        sa.Column("url", sa.Text()),
        sa.Column("extra", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("last_updated", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["place_id"], ["places.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("source", "source_place_id", name="uq_signal_source_identity"),
        sa.CheckConstraint("rating IS NULL OR rating BETWEEN 0 AND 5", name="ck_rating_range"),
        sa.CheckConstraint("review_count IS NULL OR review_count >= 0", name="ck_review_count"),
    )
    op.create_index("ix_signals_place_source", "source_signals", ["place_id", "source"])

    op.create_table(
        "text_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("place_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", source_name, nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("url", sa.Text()),
        sa.Column("sentiment", sa.Float()),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["place_id"], ["places.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("place_id", "content_hash", name="uq_evidence_place_content"),
    )
    op.create_index("ix_evidence_place", "text_evidence", ["place_id"])

    op.create_table(
        "merge_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("left_place_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("right_place_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", merge_decision, nullable=False),
        sa.Column("name_similarity", sa.Float(), nullable=False),
        sa.Column("distance_m", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["left_place_id"], ["places.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["right_place_id"], ["places.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("left_place_id", "right_place_id", name="uq_merge_pair"),
    )

    op.create_table(
        "ingest_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source", source_name, nullable=False),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("records_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("places_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("places_merged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("flagged_for_review", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_table("ingest_runs")
    op.drop_table("merge_reviews")
    op.drop_index("ix_evidence_place", table_name="text_evidence")
    op.drop_table("text_evidence")
    op.drop_index("ix_signals_place_source", table_name="source_signals")
    op.drop_table("source_signals")
    for idx in (
        "ix_places_city_active_score",
        "ix_places_name_trgm",
        "ix_places_tags",
        "ix_places_geom",
        "ix_places_duplicate_of",
        "ix_places_composite_score",
        "ix_places_name_normalized",
        "ix_places_city",
    ):
        op.drop_index(idx, table_name="places")
    op.drop_table("places")
    merge_decision.drop(bind, checkfirst=True)
    source_name.drop(bind, checkfirst=True)
    place_category.drop(bind, checkfirst=True)
