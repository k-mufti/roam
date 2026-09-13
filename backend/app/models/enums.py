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


#: Stops per day and the day's window, by pace.
#:
#: Counts include meals. An earlier version used 3/5/7, which looked reasonable
#: until the generated itineraries turned out to contain no lunch or dinner at
#: all: five stops fills 09:00-16:30 with sightseeing and the cap stops there.
#: A day needs roughly two meal slots plus the sights, so the counts are higher
#: and `meals_per_day` states the reservation explicitly.
PACE_PROFILES: dict[Pace, dict[str, int]] = {
    Pace.RELAXED: {
        "stops_per_day": 4, "day_start_hour": 10, "day_end_hour": 22, "meals_per_day": 1,
    },
    Pace.MODERATE: {
        "stops_per_day": 6, "day_start_hour": 9, "day_end_hour": 23, "meals_per_day": 2,
    },
    Pace.PACKED: {
        "stops_per_day": 8, "day_start_hour": 8, "day_end_hour": 24, "meals_per_day": 2,
    },
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
