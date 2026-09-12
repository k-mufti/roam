"""Google Places (New) v1 ingestion.

Why this source first: it is the only one that reliably supplies *all* of
coordinates, category, price tier and structured opening hours. The other three
sources are enrichment layers on top of the skeleton Google establishes — in
particular, Reddit mentions have no coordinates at all and can only attach to
places a geocoded source has already created.

Tradeoffs worth naming:

* **`searchNearby` caps at 20 results per call**, so breadth comes from issuing
  one call per `includedTypes` group rather than one big call. That is also
  why the type groups below are chosen to be disjoint.
* **Billing is per request and per field.** The `X-Goog-FieldMask` header is
  mandatory in the v1 API and directly determines the SKU charged, so it asks
  for exactly the fields the schema uses and nothing more. Responses are cached
  to disk (`ResponseCache`) so iterating on downstream code costs nothing.
* **No `nextPageToken` handling.** The v1 Nearby Search does not paginate; the
  20-per-call cap is the real ceiling. Getting more would mean tiling the city
  into sub-circles, which is a real technique but multiplies cost — out of
  scope for v1, and noted in the README as a known limitation.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.ingestion.base import RawEvidence, RawPlace, SourceAdapter
from app.ingestion.cache import ResponseCache
from app.ingestion.normalize import GOOGLE_TYPE_MAP, map_category
from app.models.enums import SourceName
from app.models.hours import from_google_periods

log = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
API_URL = "https://places.googleapis.com/v1/places:searchNearby"

FIELD_MASK = ",".join(
    f"places.{f}"
    for f in (
        "id",
        "displayName",
        "formattedAddress",
        "location",
        "types",
        "primaryType",
        "rating",
        "userRatingCount",
        "priceLevel",
        "regularOpeningHours",
        "websiteUri",
        "googleMapsUri",
        "editorialSummary",
    )
)

#: One request per group. Disjoint on purpose so results don't overlap and
#: burn quota on duplicates.
TYPE_GROUPS: tuple[tuple[str, ...], ...] = (
    ("restaurant",),
    ("cafe", "bakery"),
    ("bar", "night_club"),
    ("museum", "art_gallery"),
    ("tourist_attraction", "historical_landmark"),
    ("park",),
    ("hotel",),
)

PRICE_LEVEL_MAP = {
    "PRICE_LEVEL_FREE": 1,
    "PRICE_LEVEL_INEXPENSIVE": 1,
    "PRICE_LEVEL_MODERATE": 2,
    "PRICE_LEVEL_EXPENSIVE": 3,
    "PRICE_LEVEL_VERY_EXPENSIVE": 4,
}


class GooglePlacesAdapter(SourceAdapter):
    source = SourceName.GOOGLE_PLACES

    def __init__(self, settings, city, *, refresh: bool = False) -> None:
        super().__init__(settings, city)
        self.refresh = refresh
        self.cache = ResponseCache(settings.cache_dir)

    def has_credentials(self) -> bool:
        return bool(self.settings.google_places_api_key)

    # --- live ---------------------------------------------------------------

    def fetch_live(self) -> list[RawPlace]:
        out: list[RawPlace] = []
        seen: set[str] = set()
        with httpx.Client(timeout=20.0) as client:
            for group in TYPE_GROUPS:
                for payload in self._search(client, group):
                    pid = payload.get("id")
                    if not pid or pid in seen:
                        continue
                    seen.add(pid)
                    place = self._to_raw_place(payload)
                    if place is not None:
                        out.append(place)
        log.info("google_places: %d unique places from %d groups", len(out), len(TYPE_GROUPS))
        return out

    def _search(self, client: httpx.Client, types: tuple[str, ...]) -> list[dict[str, Any]]:
        cache_key = f"{self.city.slug}:nearby:{'+'.join(types)}"
        if not self.refresh:
            cached = self.cache.get("google_places", cache_key)
            if cached is not None:
                log.debug("google_places cache hit: %s", cache_key)
                return cached.get("places", [])

        body = {
            "includedTypes": list(types),
            "maxResultCount": 20,
            "languageCode": "en",
            "locationRestriction": {
                "circle": {
                    "center": {
                        "latitude": self.city.center_lat,
                        "longitude": self.city.center_lng,
                    },
                    "radius": float(self.city.search_radius_m),
                }
            },
        }
        response = client.post(
            API_URL,
            json=body,
            headers={
                "X-Goog-Api-Key": self.settings.google_places_api_key or "",
                "X-Goog-FieldMask": FIELD_MASK,
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        data = response.json()
        self.cache.put("google_places", cache_key, data)
        return data.get("places", [])

    # --- fixture ------------------------------------------------------------

    def fetch_fixture(self) -> list[RawPlace]:
        """Read the checked-in sample payload.

        Crucially this parses through *the same* `_to_raw_place` mapping as the
        live path — the fixture is a recorded API response, not a shortcut that
        bypasses the parser. If the mapping breaks, fixture mode breaks too,
        which is what makes mock mode a real test of the code.
        """
        path = FIXTURE_DIR / f"google_places_{self.city.slug}.json"
        if not path.exists():
            log.warning("no google_places fixture for %s", self.city.slug)
            return []
        data = json.loads(path.read_text("utf-8"))
        out = []
        for payload in data.get("places", []):
            place = self._to_raw_place(payload)
            if place is not None:
                out.append(place)
        return out

    # --- mapping ------------------------------------------------------------

    def _to_raw_place(self, payload: dict[str, Any]) -> RawPlace | None:
        location = payload.get("location") or {}
        lat, lng = location.get("latitude"), location.get("longitude")
        if lat is None or lng is None:
            return None

        name = (payload.get("displayName") or {}).get("text")
        if not name:
            return None

        types = list(payload.get("types") or [])
        primary = payload.get("primaryType")
        category = map_category(types, GOOGLE_TYPE_MAP, primary=primary)

        summary = (payload.get("editorialSummary") or {}).get("text")
        evidence = []
        if summary:
            evidence.append(RawEvidence(text=summary, url=payload.get("googleMapsUri"), weight=1.0))

        return RawPlace(
            source=self.source,
            source_place_id=payload["id"],
            name=name,
            city=self.city.name,
            country_code=self.city.country_code,
            category=category,
            lat=float(lat),
            lng=float(lng),
            address=payload.get("formattedAddress"),
            price_tier=PRICE_LEVEL_MAP.get(payload.get("priceLevel") or ""),
            hours=from_google_periods((payload.get("regularOpeningHours") or {}).get("periods")),
            rating=payload.get("rating"),
            raw_rating=payload.get("rating"),
            review_count=payload.get("userRatingCount"),
            url=payload.get("websiteUri") or payload.get("googleMapsUri"),
            extra={
                "types": types[:8],
                "primary_type": primary,
                "maps_uri": payload.get("googleMapsUri"),
            },
            # Google's aggregate rating is a rolling current value, so the
            # signal's observation time is the fetch time. Contrast with
            # Reddit, where observed_at is the thread's creation date and the
            # scoring module decays it accordingly.
            observed_at=datetime.now(UTC),
            evidence=evidence,
        )
