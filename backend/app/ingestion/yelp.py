"""Yelp Fusion v3 ingestion.

Yelp's value here is *corroboration*, not coverage. Its Madrid catalogue is
noticeably thinner than its US catalogue, and a chunk of Spanish restaurants
have a handful of reviews where Google has thousands. That is fine — the whole
point of the scoring module is that a second independent source agreeing with
the first is worth more than either source's raw stars. Yelp is also where
`price` and review text come from most cheaply.

Rate limit: 500 requests/day on the free tier. That budget drives two choices:

* **Search is the primary call** (one per category group, 50 results each) —
  cheap and broad.
* **Detail and review calls are capped** by `detail_limit`. `/businesses/{id}`
  is the only place Yelp exposes opening hours, and `/businesses/{id}/reviews`
  is the only place it exposes review text, but both are one request *per
  business*. Pulling them for all ~200 results would burn the daily budget in
  one run, so we enrich only the top N by review count — the ones whose text
  actually matters to tagging.
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
from app.ingestion.normalize import YELP_ALIAS_MAP, map_category
from app.models.enums import SourceName
from app.models.hours import from_yelp_open

log = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SEARCH_URL = "https://api.yelp.com/v3/businesses/search"
DETAIL_URL = "https://api.yelp.com/v3/businesses/{id}"
REVIEWS_URL = "https://api.yelp.com/v3/businesses/{id}/reviews"

#: One search per group. Yelp's `categories` filter is OR-ed within a call.
CATEGORY_GROUPS: tuple[tuple[str, ...], ...] = (
    ("restaurants",),
    ("cafes", "coffee", "bakeries"),
    ("bars", "cocktailbars", "wine_bars"),
    ("danceclubs", "nightlife"),
    ("museums", "landmarks", "parks"),
    ("hotels",),
)

#: How many businesses get the (expensive, per-business) hours+reviews calls.
DEFAULT_DETAIL_LIMIT = 15


class YelpAdapter(SourceAdapter):
    source = SourceName.YELP

    def __init__(self, settings, city, *, refresh: bool = False, detail_limit: int | None = None) -> None:
        super().__init__(settings, city)
        self.refresh = refresh
        self.detail_limit = DEFAULT_DETAIL_LIMIT if detail_limit is None else detail_limit
        self.cache = ResponseCache(settings.cache_dir)

    def has_credentials(self) -> bool:
        return bool(self.settings.yelp_api_key)

    # --- live ---------------------------------------------------------------

    def fetch_live(self) -> list[RawPlace]:
        headers = {"Authorization": f"Bearer {self.settings.yelp_api_key}"}
        payloads: dict[str, dict[str, Any]] = {}
        with httpx.Client(timeout=20.0, headers=headers) as client:
            for group in CATEGORY_GROUPS:
                for business in self._search(client, group):
                    bid = business.get("id")
                    if bid and bid not in payloads:
                        payloads[bid] = business

            # Spend the per-business budget where the text is richest.
            ranked = sorted(
                payloads.values(), key=lambda b: b.get("review_count") or 0, reverse=True
            )
            for business in ranked[: self.detail_limit]:
                self._enrich(client, business)

        return [p for p in (self._to_raw_place(b) for b in payloads.values()) if p is not None]

    def _search(self, client: httpx.Client, categories: tuple[str, ...]) -> list[dict[str, Any]]:
        cache_key = f"{self.city.slug}:search:{'+'.join(categories)}"
        if not self.refresh:
            cached = self.cache.get("yelp", cache_key)
            if cached is not None:
                return cached.get("businesses", [])
        response = client.get(
            SEARCH_URL,
            params={
                "latitude": self.city.center_lat,
                "longitude": self.city.center_lng,
                # Yelp caps radius at 40km; ours is well under.
                "radius": min(self.city.search_radius_m, 40_000),
                "categories": ",".join(categories),
                "limit": 50,
                "sort_by": "review_count",
                "locale": "en_US",
            },
        )
        response.raise_for_status()
        data = response.json()
        self.cache.put("yelp", cache_key, data)
        return data.get("businesses", [])

    def _enrich(self, client: httpx.Client, business: dict[str, Any]) -> None:
        """Attach hours and review snippets. Failures here are non-fatal: the
        business is still usable as a rating signal without them."""
        bid = business["id"]
        for kind, url, key in (
            ("detail", DETAIL_URL.format(id=bid), "hours"),
            ("reviews", REVIEWS_URL.format(id=bid), "reviews"),
        ):
            cache_key = f"{self.city.slug}:{kind}:{bid}"
            cached = None if self.refresh else self.cache.get("yelp", cache_key)
            if cached is None:
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    cached = resp.json()
                    self.cache.put("yelp", cache_key, cached)
                except httpx.HTTPError as exc:
                    log.debug("yelp %s failed for %s: %s", kind, bid, exc)
                    continue
            if key in cached:
                business[key] = cached[key]

    # --- fixture ------------------------------------------------------------

    def fetch_fixture(self) -> list[RawPlace]:
        path = FIXTURE_DIR / f"yelp_{self.city.slug}.json"
        if not path.exists():
            log.warning("no yelp fixture for %s", self.city.slug)
            return []
        data = json.loads(path.read_text("utf-8"))
        return [
            p
            for p in (self._to_raw_place(b) for b in data.get("businesses", []))
            if p is not None
        ]

    # --- mapping ------------------------------------------------------------

    def _to_raw_place(self, business: dict[str, Any]) -> RawPlace | None:
        coords = business.get("coordinates") or {}
        lat, lng = coords.get("latitude"), coords.get("longitude")
        name = business.get("name")
        bid = business.get("id")
        if lat is None or lng is None or not name or not bid:
            return None

        aliases = [c.get("alias", "") for c in business.get("categories") or []]
        titles = [c.get("title", "") for c in business.get("categories") or []]

        location = business.get("location") or {}
        address = ", ".join(business.get("location", {}).get("display_address") or []) or None

        # Yelp renders price in local currency: "$$" in the US, "€€" in Spain.
        # Length is the tier either way.
        price = business.get("price") or ""
        price_tier = len(price) if 1 <= len(price) <= 4 else None

        evidence = [
            RawEvidence(text=r["text"], url=r.get("url"), weight=1.0)
            for r in business.get("reviews") or []
            if r.get("text")
        ]

        hours_block = business.get("hours") or []
        hours = from_yelp_open(hours_block[0].get("open")) if hours_block else None

        return RawPlace(
            source=self.source,
            source_place_id=bid,
            name=name,
            city=self.city.name,
            country_code=location.get("country") or self.city.country_code,
            category=map_category(aliases + titles, YELP_ALIAS_MAP),
            lat=float(lat),
            lng=float(lng),
            address=address,
            neighborhood=location.get("city"),
            price_tier=price_tier,
            hours=hours,
            rating=business.get("rating"),
            raw_rating=business.get("rating"),
            review_count=business.get("review_count"),
            url=business.get("url"),
            extra={
                "alias": business.get("alias"),
                "categories": aliases[:6],
                "price_display": price or None,
                "is_closed": business.get("is_closed"),
            },
            # Like Google, Yelp's rating is a current rolling aggregate.
            observed_at=datetime.now(UTC),
            evidence=evidence,
        )
