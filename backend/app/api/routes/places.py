"""Place listing, filtering and score explanation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas import FacetsOut, PlaceOut, ScoreBreakdownOut, SourceSignalOut
from app.config import Settings, get_settings
from app.db import get_db
from app.models import Place, PlaceCategory, SourceSignal

router = APIRouter(tags=["places"])


def _to_out(place: Place) -> PlaceOut:
    return PlaceOut(
        id=str(place.id),
        name=place.name,
        city=place.city,
        lat=place.lat,
        lng=place.lng,
        category=place.category,
        price_tier=place.price_tier,
        tags=list(place.tags or []),
        hours=place.hours,
        composite_score=place.composite_score,
        address=place.address,
        neighborhood=place.neighborhood,
        source_signals=[
            SourceSignalOut(
                source=s.source,
                rating=s.rating,
                review_count=s.review_count,
                url=s.url,
                extra=dict(s.extra or {}),
                last_updated=s.last_updated,
            )
            for s in place.signals
        ],
        source_count=len({s.source for s in place.signals}),
    )


@router.get("/places", response_model=list[PlaceOut])
def list_places(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    category: list[PlaceCategory] | None = Query(default=None),
    tag: list[str] | None = Query(default=None, description="Repeatable. OR-ed together."),
    max_price_tier: int | None = Query(default=None, ge=1, le=4),
    min_score: float | None = Query(default=None, ge=0, le=100),
    min_sources: int | None = Query(default=None, ge=1, le=4),
    lat: float | None = Query(default=None, description="Radius filter centre"),
    lng: float | None = None,
    radius_m: float | None = Query(default=None, ge=100, le=50_000),
    limit: int = Query(default=500, ge=1, le=2000),
):
    """Filtered places for the map.

    The radius filter runs in PostGIS via ST_DWithin on the geography column, so
    it takes metres directly and uses the GiST index — this is the spec's
    "3-5 mile radius" view.
    """
    stmt = (
        select(Place)
        .options(selectinload(Place.signals))
        .where(Place.city == settings.city.name, Place.duplicate_of.is_(None))
    )

    if category:
        stmt = stmt.where(Place.category.in_(list(category)))
    if tag:
        stmt = stmt.where(Place.tags.overlap(list(tag)))
    if max_price_tier is not None:
        stmt = stmt.where((Place.price_tier.is_(None)) | (Place.price_tier <= max_price_tier))
    if min_score is not None:
        stmt = stmt.where(Place.composite_score >= min_score)
    if lat is not None and lng is not None and radius_m:
        point = func.ST_GeogFromText(f"SRID=4326;POINT({lng} {lat})")
        stmt = stmt.where(func.ST_DWithin(Place.geom, point, radius_m))

    if min_sources is not None and min_sources > 1:
        corroborated = (
            select(SourceSignal.place_id)
            .group_by(SourceSignal.place_id)
            .having(func.count(distinct(SourceSignal.source)) >= min_sources)
            .scalar_subquery()
        )
        stmt = stmt.where(Place.id.in_(corroborated))

    stmt = stmt.order_by(Place.composite_score.desc().nullslast()).limit(limit)
    return [_to_out(p) for p in db.execute(stmt).scalars()]


@router.get("/places/{place_id}/score", response_model=ScoreBreakdownOut)
def explain_score(place_id: str, db: Session = Depends(get_db)):
    """The persisted score breakdown: per-source contributions and weights."""
    place = db.get(Place, place_id)
    if place is None:
        raise HTTPException(status_code=404, detail="place not found")
    return ScoreBreakdownOut(
        place_id=str(place.id),
        name=place.name,
        composite_score=place.composite_score,
        breakdown=place.score_breakdown,
    )


@router.get("/facets", response_model=FacetsOut)
def facets(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Filter options derived from the data present, not hardcoded in the UI."""
    city = settings.city.name
    base = (Place.city == city, Place.duplicate_of.is_(None))

    categories = db.execute(
        select(Place.category, func.count()).where(*base).group_by(Place.category)
    ).all()
    tag_rows = db.execute(
        select(func.unnest(Place.tags).label("tag"), func.count())
        .where(*base)
        .group_by("tag")
        .order_by(func.count().desc())
    ).all()
    prices = db.execute(
        select(Place.price_tier, func.count())
        .where(*base, Place.price_tier.isnot(None))
        .group_by(Place.price_tier)
        .order_by(Place.price_tier)
    ).all()
    lo, hi = db.execute(
        select(func.min(Place.composite_score), func.max(Place.composite_score)).where(*base)
    ).one()
    total = db.scalar(select(func.count()).select_from(Place).where(*base)) or 0
    sources = db.execute(
        select(SourceSignal.source, func.count())
        .join(Place, Place.id == SourceSignal.place_id)
        .where(*base)
        .group_by(SourceSignal.source)
        .order_by(func.count().desc())
    ).all()

    return FacetsOut(
        city=city,
        center={"lat": settings.city.center_lat, "lng": settings.city.center_lng},
        categories=[{"value": c.value, "count": n} for c, n in categories],
        tags=[{"value": t, "count": n} for t, n in tag_rows],
        price_tiers=[{"value": p, "count": n} for p, n in prices],
        score_range={"min": float(lo or 0), "max": float(hi or 0)},
        place_count=total,
        sources=[{"value": s.value, "count": n} for s, n in sources],
    )
