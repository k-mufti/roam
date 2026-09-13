"""Itinerary generation endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import (
    DroppedOut,
    ItineraryDayOut,
    ItineraryOut,
    ItineraryRequest,
    ItineraryStopOut,
)
from app.config import Settings, get_settings
from app.db import get_db
from app.optimizer.itinerary import Itinerary, build_itinerary

router = APIRouter(tags=["itinerary"])


def _serialize(itinerary: Itinerary) -> ItineraryOut:
    return ItineraryOut(
        city=itinerary.city,
        start_date=itinerary.start_date,
        pace=itinerary.pace,
        travel_mode=itinerary.travel_mode,
        routing_provider=itinerary.provider_name,
        pool_size=itinerary.pool_size,
        requested_tags=list(itinerary.requested_tags),
        max_price_tier=itinerary.max_price_tier,
        total_stops=itinerary.total_stops,
        total_travel_minutes=round(itinerary.total_travel_minutes, 1),
        days=[
            ItineraryDayOut(
                day_index=day.day_index,
                date=day.day_date,
                weekday=day.weekday,
                travel_minutes=round(day.total_travel_seconds / 60.0, 1),
                dwell_minutes=day.total_dwell_minutes,
                free_minutes=day.total_free_minutes,
                stops=[
                    ItineraryStopOut(
                        position=position,
                        place_id=scheduled.stop.key,
                        name=scheduled.stop.name,
                        lat=scheduled.stop.lat,
                        lng=scheduled.stop.lng,
                        category=scheduled.stop.category,
                        price_tier=scheduled.stop.price_tier,
                        tags=list(scheduled.stop.tags),
                        composite_score=scheduled.stop.score,
                        start_time=scheduled.start_time,
                        end_time=scheduled.end_time,
                        crosses_midnight=scheduled.crosses_midnight,
                        travel_minutes_from_previous=round(scheduled.travel_seconds / 60.0, 1),
                        wait_minutes=scheduled.wait_minutes,
                        free_minutes=scheduled.free_minutes,
                        hours_assumed=scheduled.hours_assumed,
                    )
                    for position, scheduled in enumerate(day.stops, start=1)
                ],
            )
            for day in itinerary.days
        ],
        not_scheduled=[
            DroppedOut(name=stop.name, reason=reason) for stop, reason in itinerary.unused[:40]
        ],
    )


@router.post("/itinerary", response_model=ItineraryOut)
def generate_itinerary(
    request: ItineraryRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Generate a day-by-day itinerary.

    Returns both the schedule and everything needed to draw it: each stop's
    coordinates in visiting order, so the client can render one polyline per day.
    """
    center = (
        (request.center_lat, request.center_lng)
        if request.center_lat is not None and request.center_lng is not None
        else None
    )
    itinerary = build_itinerary(
        db,
        city=settings.city,
        start_date=request.start_date,
        days=request.days,
        pace=request.pace,
        tags=tuple(request.tags),
        avoid_tags=tuple(request.avoid_tags),
        max_price_tier=request.max_price_tier,
        center=center,
        radius_m=request.radius_m,
        settings=settings,
    )
    return _serialize(itinerary)
