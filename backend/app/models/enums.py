"""Controlled vocabularies shared by the DB, ingestion, and API layers."""

from __future__ import annotations

from enum import StrEnum


class PlaceCategory(StrEnum):
    """Normalized category. Every source has its own taxonomy (Google has ~100
    `types`, Yelp has ~1500 aliases); each adapter maps down into this set so
    filtering and the optimizer's day-shaping logic have something stable."""

    RESTAURANT = "restaurant"
    CAFE = "cafe"
    BAR = "bar"
    ATTRACTION = "attraction"
    MUSEUM = "museum"
    PARK = "park"
    SHOPPING = "shopping"
    HOTEL = "hotel"
    NIGHTLIFE = "nightlife"
    OTHER = "other"


#: Categories that represent "somewhere you sleep" rather than "somewhere you
#: go". The optimizer excludes these from itinerary stops but still surfaces
#: them on the map.
LODGING_CATEGORIES = frozenset({PlaceCategory.HOTEL})


class SourceName(StrEnum):
    GOOGLE_PLACES = "google_places"
    YELP = "yelp"
    REDDIT = "reddit"
    BLOG = "blog"


class IngestMode(StrEnum):
    """Whether an adapter hit the network or read checked-in fixtures."""

    LIVE = "live"
    FIXTURE = "fixture"


class MergeDecision(StrEnum):
    MERGED = "merged"
    CREATED = "created"
    FLAGGED = "flagged"  # ambiguous: recorded for human review, not merged


class Pace(StrEnum):
    RELAXED = "relaxed"
    MODERATE = "moderate"
    PACKED = "packed"


#: Stops per day, and target minutes on-site, by pace.
PACE_PROFILES: dict[Pace, dict[str, int]] = {
    Pace.RELAXED: {"stops_per_day": 3, "day_start_hour": 10, "day_end_hour": 21},
    Pace.MODERATE: {"stops_per_day": 5, "day_start_hour": 9, "day_end_hour": 22},
    Pace.PACKED: {"stops_per_day": 7, "day_start_hour": 8, "day_end_hour": 23},
}

#: Default dwell time per category, in minutes. Used when a place has no
#: source-provided duration hint.
DEFAULT_DWELL_MINUTES: dict[PlaceCategory, int] = {
    PlaceCategory.RESTAURANT: 75,
    PlaceCategory.CAFE: 40,
    PlaceCategory.BAR: 60,
    PlaceCategory.ATTRACTION: 75,
    PlaceCategory.MUSEUM: 105,
    PlaceCategory.PARK: 60,
    PlaceCategory.SHOPPING: 50,
    PlaceCategory.HOTEL: 0,
    PlaceCategory.NIGHTLIFE: 90,
    PlaceCategory.OTHER: 60,
}
