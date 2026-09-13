"""API response/request models.

Note `PlaceOut.source_signals` is a list, matching how the spec describes the
`Place` schema, even though it is a child table relationally. The API is the
right place to present it that way.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import Pace, PlaceCategory, SourceName


class SourceSignalOut(BaseModel):
    source: SourceName
    rating: float | None = None
    review_count: int | None = None
    url: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    last_updated: Any | None = None


class PlaceOut(BaseModel):
    id: str
    name: str
    city: str
    lat: float
    lng: float
    category: PlaceCategory
    price_tier: int | None = None
    tags: list[str] = Field(default_factory=list)
    hours: dict[str, Any] | None = None
    composite_score: float | None = None
    address: str | None = None
    neighborhood: str | None = None
    source_signals: list[SourceSignalOut] = Field(default_factory=list)
    #: Number of distinct sources — the corroboration count, surfaced because it
    #: is the most legible justification for a ranking.
    source_count: int = 0


class ScoreBreakdownOut(BaseModel):
    place_id: str
    name: str
    composite_score: float | None
    breakdown: dict[str, Any] | None


class FacetsOut(BaseModel):
    """Everything the UI needs to build its filter controls, derived from the
    data actually present rather than hardcoded in the frontend."""

    city: str
    center: dict[str, float]
    categories: list[dict[str, Any]]
    tags: list[dict[str, Any]]
    price_tiers: list[dict[str, Any]]
    score_range: dict[str, float]
    place_count: int
    sources: list[dict[str, Any]]


class ItineraryStopOut(BaseModel):
    position: int
    place_id: str
    name: str
    lat: float
    lng: float
    category: PlaceCategory
    price_tier: int | None = None
    tags: list[str] = Field(default_factory=list)
    composite_score: float | None = None
    start_time: time
    end_time: time
    #: True when end_time falls on the following calendar day.
    crosses_midnight: bool = False
    travel_minutes_from_previous: float = 0.0
    wait_minutes: int = 0
    free_minutes: int = 0
    #: True when the slot was chosen against a category default because the
    #: place has no hours data.
    hours_assumed: bool = False


class ItineraryDayOut(BaseModel):
    day_index: int
    date: date
    weekday: int
    stops: list[ItineraryStopOut] = Field(default_factory=list)
    travel_minutes: float = 0.0
    dwell_minutes: int = 0
    free_minutes: int = 0


class DroppedOut(BaseModel):
    name: str
    reason: str


class ItineraryOut(BaseModel):
    city: str
    start_date: date
    pace: Pace
    travel_mode: str
    routing_provider: str
    pool_size: int
    requested_tags: list[str] = Field(default_factory=list)
    max_price_tier: int = 4
    days: list[ItineraryDayOut] = Field(default_factory=list)
    total_stops: int = 0
    total_travel_minutes: float = 0.0
    not_scheduled: list[DroppedOut] = Field(default_factory=list)


class ItineraryRequest(BaseModel):
    start_date: date
    days: int = Field(default=3, ge=1, le=14)
    pace: Pace = Pace.MODERATE
    tags: list[str] = Field(default_factory=list)
    avoid_tags: list[str] = Field(default_factory=list)
    max_price_tier: int = Field(default=4, ge=1, le=4)
    center_lat: float | None = None
    center_lng: float | None = None
    radius_m: float | None = Field(default=None, ge=100, le=50_000)
