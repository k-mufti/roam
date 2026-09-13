"""Application configuration.

Single source of truth for environment-derived settings. Note the deliberate
absence of any hardcoded "Madrid" literal outside of `CITIES` below: the city
is a real, injected value everywhere downstream (schema column, query filter,
ingestion argument), so adding a second city later means appending to `CITIES`
and running ingestion — not hunting string literals through the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent


@dataclass(frozen=True)
class CityConfig:
    """Geographic + search parameters for one supported city.

    `center` is used as the map centroid and as the anchor for radius-based
    source queries. `search_radius_m` bounds ingestion so we don't pull in
    suburbs 40km out. `subreddits` and `reddit_queries` are per-city because
    the useful communities differ wildly between cities.
    """

    slug: str
    name: str
    country_code: str
    center_lat: float
    center_lng: float
    search_radius_m: int
    timezone: str
    subreddits: tuple[str, ...]
    blog_sources: tuple[str, ...]


# v1 ships exactly one city on purpose (see README "Scope"). The structure is
# a dict keyed by name so this is additive, not a rewrite.
CITIES: dict[str, CityConfig] = {
    "Madrid": CityConfig(
        slug="madrid",
        name="Madrid",
        country_code="ES",
        center_lat=40.4168,
        center_lng=-3.7038,
        search_radius_m=8000,
        timezone="Europe/Madrid",
        subreddits=("madrid", "spain", "askspain", "travel"),
        blog_sources=(
            "wikivoyage-madrid",
            "wikivoyage-madrid-salamanca",
            "wikivoyage-madrid-chamberi",
            "wikivoyage-madrid-retiro",
            "wikivoyage-madrid-moncloa",
            "wikivoyage-madrid-arganzuela",
        ),
    ),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://trip:trip@localhost:55432/trip_package"
    target_city: str = "Madrid"

    google_places_api_key: str | None = None
    yelp_api_key: str | None = None
    reddit_client_id: str | None = None
    reddit_client_secret: str | None = None
    reddit_user_agent: str = "trip-package/0.1 (portfolio project)"

    #: `haversine` | `osrm` | `google`. Defaults to haversine — see
    #: app/optimizer/travel.py for why the public OSRM demo is not usable for
    #: pedestrian routing.
    routing_provider: str = "haversine"
    osrm_base_url: str = "https://router.project-osrm.org"
    #: Escape hatch to use the public OSRM demo anyway, knowing it is car-only.
    osrm_allow_public_demo: bool = False
    google_directions_api_key: str | None = None

    travel_mode: str = "walk"

    ingestion_force_fixtures: bool = False

    cache_dir: Path = BACKEND_ROOT / ".cache"

    @property
    def city(self) -> CityConfig:
        try:
            return CITIES[self.target_city]
        except KeyError as exc:  # pragma: no cover - config error path
            raise ValueError(
                f"TARGET_CITY={self.target_city!r} is not configured. "
                f"Known cities: {', '.join(CITIES)}"
            ) from exc


@lru_cache
def get_settings() -> Settings:
    return Settings()
