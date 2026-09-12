"""Scraped ingestion — the supplementary source.

**This source is never load-bearing.** Per the spec it exists to demonstrate
scraping, so everything about it is built to fail quietly: `soft_fail` is on,
the adapter degrades to fixtures on any error, and nothing downstream assumes
it ran. If the target site restructures tomorrow, the app still works.

**Target choice.** The obvious pick would be a "best restaurants in Madrid"
listicle, and the parser below would handle one. It scrapes Wikivoyage instead,
for reasons that are the actual engineering content of this module:

* **robots.txt.** Most travel-media sites disallow automated access, and
  Reddit's is a blanket `Disallow: /`. Wikivoyage explicitly permits
  `/wiki/<article>` for `User-agent: *`. This adapter checks robots at runtime
  rather than taking my word for it — see `_robots_allows`.
* **Structured markup.** Wikivoyage listings use hCard microformat
  (`span.vcard` with `.listing-name`, `.geo`, `.listing-hours`, ...), so the
  scraper extracts *coordinates and opening hours*, not just names. That makes
  this a geocoded source that can create places, unlike Reddit.
* **Licensing.** CC BY-SA, so the extracted text can be stored as evidence and
  shown in the UI with attribution.

The parser is deliberately defensive: every field is optional, a listing
missing coordinates is skipped rather than guessed at, and a changed class name
degrades to fewer listings rather than an exception.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.robotparser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.ingestion.base import RawEvidence, RawPlace, SourceAdapter
from app.ingestion.cache import ResponseCache
from app.ingestion.normalize import synthetic_source_id
from app.models.enums import PlaceCategory, SourceName
from app.models.hours import DAY_KEYS

log = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).parent / "fixtures"

#: Registered scrape targets, keyed by the id used in `CityConfig.blog_sources`.
#: Adding a new site means adding an entry and a parser, not touching the
#: ingestion pipeline.
#:
#: Madrid is a "huge city" article on Wikivoyage: the parent page carries only
#: the headline sights, and the Eat/Drink/Sleep listings live on district
#: subpages. Hence one target per district. (Madrid/Centro exists but currently
#: has no structured listings, so it is not registered — an empty fetch is
#: wasted politeness budget.)
TARGETS: dict[str, dict[str, Any]] = {
    tid: {
        "url": f"https://en.wikivoyage.org/wiki/{article}",
        "parser": "wikivoyage",
        "attribution": "Wikivoyage, CC BY-SA 4.0",
    }
    for tid, article in (
        ("wikivoyage-madrid", "Madrid"),
        ("wikivoyage-madrid-salamanca", "Madrid/Salamanca"),
        ("wikivoyage-madrid-chamberi", "Madrid/Chamber%C3%AD"),
        ("wikivoyage-madrid-retiro", "Madrid/Retiro"),
        ("wikivoyage-madrid-moncloa", "Madrid/Moncloa"),
        ("wikivoyage-madrid-arganzuela", "Madrid/Arganzuela"),
    )
}

#: Wikivoyage section heading -> our category. Sections not listed (Get in,
#: Get around, Connect, Cope) are transport/logistics and are skipped entirely.
SECTION_CATEGORY = {
    "see": PlaceCategory.ATTRACTION,
    "do": PlaceCategory.ATTRACTION,
    "eat": PlaceCategory.RESTAURANT,
    "drink": PlaceCategory.BAR,
    "sleep": PlaceCategory.HOTEL,
    "buy": PlaceCategory.SHOPPING,
}
# Note: "Learn" is deliberately absent. It lists language schools and
# universities, which parse cleanly but are not places a traveller visits.

#: "€5", "€€", "Free" -> price tier. Wikivoyage prices are free text, so this
#: is best-effort and returns None rather than guessing.
_PRICE_SYMBOL_RE = re.compile(r"^[€$]{1,4}$")
_PRICE_AMOUNT_RE = re.compile(r"[€$]\s?(\d+(?:[.,]\d+)?)")

#: "Mon-Sat 10:00-22:00, Sun 11:00-21:00"
_DAY_ALIASES = {
    "mon": 0, "tue": 1, "tues": 1, "wed": 2, "weds": 2, "thu": 3, "thur": 3,
    "thurs": 3, "fri": 4, "sat": 5, "sun": 6, "daily": -1, "m": 0, "w": 2,
}
_HOURS_CLAUSE_RE = re.compile(
    r"(?P<days>(?:daily|[A-Z][a-z]{1,4})(?:\s*[-–]\s*[A-Z][a-z]{1,4})?)\s*"
    r"(?P<open>\d{1,2}:\d{2})\s*[-–]\s*(?P<close>\d{1,2}:\d{2})",
    re.IGNORECASE,
)

REQUEST_TIMEOUT = 25.0
#: Pause between requests to the same host. Not required by robots.txt here,
#: but hammering a volunteer-run wiki with back-to-back requests is rude and
#: gets scrapers blocked.
REQUEST_DELAY_SECONDS = 0.75


class BlogScraperAdapter(SourceAdapter):
    source = SourceName.BLOG
    soft_fail = True

    def __init__(self, settings, city, *, refresh: bool = False) -> None:
        super().__init__(settings, city)
        self.refresh = refresh
        self.cache = ResponseCache(settings.cache_dir)

    def has_credentials(self) -> bool:
        """No key needed — but there must be a registered target for the city."""
        return any(t in TARGETS for t in self.city.blog_sources) or bool(
            _targets_for_city(self.city)
        )

    # --- live ---------------------------------------------------------------

    def fetch_live(self) -> list[RawPlace]:
        out: list[RawPlace] = []
        headers = {"User-Agent": self.settings.reddit_user_agent}
        targets = _targets_for_city(self.city)
        with httpx.Client(
            timeout=REQUEST_TIMEOUT, headers=headers, follow_redirects=True
        ) as client:
            for index, (target_id, target) in enumerate(targets.items()):
                url = target["url"]
                if not _robots_allows(client, url, headers["User-Agent"]):
                    log.warning("robots.txt disallows %s; skipping target %s", url, target_id)
                    continue
                if index and not self.cache.get("blog", f"{self.city.slug}:{target_id}"):
                    time.sleep(REQUEST_DELAY_SECONDS)
                html = self._get_html(client, target_id, url)
                if html is None:
                    continue
                out.extend(self._parse(html, target_id, target))
        log.info("blog: %d listings across %d target(s)", len(out), len(targets))
        return out

    def _get_html(self, client: httpx.Client, target_id: str, url: str) -> str | None:
        cache_key = f"{self.city.slug}:{target_id}"
        if not self.refresh:
            cached = self.cache.get("blog", cache_key)
            if cached is not None:
                return cached.get("html")
        try:
            response = client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("scrape failed for %s: %s", url, exc)
            return None
        self.cache.put("blog", cache_key, {"html": response.text, "url": url})
        return response.text

    # --- fixture ------------------------------------------------------------

    def fetch_fixture(self) -> list[RawPlace]:
        """Replay saved HTML through the *same* parser as live mode.

        The fixture stores raw HTML rather than parsed records specifically so
        that mock mode tests the scraper, not a shortcut around it.
        """
        path = FIXTURE_DIR / f"blog_{self.city.slug}.json"
        if not path.exists():
            log.warning("no blog fixture for %s", self.city.slug)
            return []
        data = json.loads(path.read_text("utf-8"))
        out: list[RawPlace] = []
        for target_id, entry in (data.get("targets") or {}).items():
            target = TARGETS.get(target_id, {"url": entry.get("url", ""), "parser": "wikivoyage",
                                             "attribution": entry.get("attribution", "")})
            out.extend(self._parse(entry["html"], target_id, target))
        return out

    # --- parsing ------------------------------------------------------------

    def _parse(self, html: str, target_id: str, target: dict[str, Any]) -> list[RawPlace]:
        soup = BeautifulSoup(html, "lxml")
        out: list[RawPlace] = []
        base_url = target.get("url", "")

        for card in soup.select(".vcard"):
            name_el = card.select_one(".listing-name")
            if name_el is None:
                continue
            name = _clean_name(name_el.get_text(" ", strip=True))
            if not name:
                continue

            lat, lng = _coordinates(card)
            if lat is None or lng is None:
                # No coordinates: cannot be mapped or routed. The name-only
                # path exists for Reddit, but a gazetteer-style source with
                # missing geo is more likely a parse failure than a real
                # mention, so skip rather than guess.
                continue

            section = _section_of(card)
            category = SECTION_CATEGORY.get(section or "")
            if category is None:
                continue  # transport/logistics section

            description = _field(card, ".listing-content")
            address = _field(card, ".listing-address")
            hours_text = _field(card, ".listing-hours")
            price_text = _field(card, ".listing-price")
            url = _listing_url(card, base_url)

            evidence = []
            if description:
                evidence.append(
                    RawEvidence(text=description, url=url or base_url, weight=1.0)
                )

            out.append(
                RawPlace(
                    source=self.source,
                    source_place_id=synthetic_source_id(
                        "blog", target_id, name.lower(), f"{lat:.5f},{lng:.5f}"
                    ),
                    name=name,
                    city=self.city.name,
                    country_code=self.city.country_code,
                    category=category,
                    lat=lat,
                    lng=lng,
                    address=address,
                    price_tier=_price_tier(price_text),
                    hours=_parse_hours(hours_text),
                    # An editorial list is a recommendation, not a rating.
                    # Inventing stars here would be fabrication; the scoring
                    # module treats inclusion itself as the signal.
                    rating=None,
                    raw_rating=None,
                    review_count=None,
                    url=url or base_url,
                    extra={
                        "target": target_id,
                        "section": section,
                        "attribution": target.get("attribution"),
                        "editorially_listed": True,
                        "price_text": price_text,
                        "hours_text": hours_text,
                    },
                    observed_at=datetime.now(UTC),
                    evidence=evidence,
                )
            )
        return out


# --- target selection -------------------------------------------------------


def _targets_for_city(city) -> dict[str, dict[str, Any]]:
    """Registered targets whose id the city config opted into.

    `CityConfig.blog_sources` holds short ids ("timeout-madrid"); a target is
    included if its key starts with one of them or matches the city slug.
    """
    wanted = set(city.blog_sources)
    return {
        tid: t
        for tid, t in TARGETS.items()
        if tid in wanted or tid.endswith(f"-{city.slug}")
    }


def _robots_allows(client: httpx.Client, url: str, user_agent: str) -> bool:
    """Check robots.txt before fetching. Failure to read robots is treated as
    allowed — that is the conventional interpretation, and the alternative
    (fail closed) would silently disable the source on a transient error."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = client.get(robots_url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.info("could not read %s (%s); proceeding", robots_url, exc)
        return True
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser.can_fetch(user_agent, url)


# --- field extraction -------------------------------------------------------


#: MediaWiki artefacts that end up inside listing names: maintenance
#: annotations ("[ dead link ]", "[ formerly ... ]") and reference markers
#: ("[1]"). Left in, they pollute both the display name and the matching key —
#: "[ dead link ] Las Tablas" normalizes to "dead link las tablas".
_BRACKETED_RE = re.compile(r"\[[^\]]*\]")


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" .,;:-–")


def _clean_name(value: str) -> str:
    return _clean_text(_BRACKETED_RE.sub(" ", value or ""))


def _field(card: Tag, selector: str) -> str | None:
    el = card.select_one(selector)
    if el is None:
        return None
    return _clean_text(el.get_text(" ", strip=True)) or None


def _coordinates(card: Tag) -> tuple[float | None, float | None]:
    geo = card.select_one(".geo")
    if geo is not None:
        lat_el, lng_el = geo.select_one(".latitude"), geo.select_one(".longitude")
        if lat_el is not None and lng_el is not None:
            try:
                return float(lat_el.get_text(strip=True)), float(lng_el.get_text(strip=True))
            except ValueError:
                pass
    # Kartographer maplinks carry the same data as attributes.
    link = card.select_one("[data-lat][data-lon]")
    if link is not None:
        try:
            return float(link["data-lat"]), float(link["data-lon"])
        except (ValueError, KeyError):
            pass
    return None, None


def _section_of(card: Tag) -> str | None:
    """Lowercased heading of the article section the listing sits in.

    MediaWiki's current HTML wraps content in nested <section> elements, so
    walking back to the nearest previous <h2> can escape into the table of
    contents. This finds the enclosing section first and reads its own heading.
    """
    for ancestor in card.parents:
        if getattr(ancestor, "name", None) != "section":
            continue
        heading = ancestor.find(["h2", "h3"], recursive=True)
        if heading is not None:
            text = heading.get_text(" ", strip=True).lower()
            for key in SECTION_CATEGORY:
                if text.startswith(key):
                    return key
            return text.split()[0] if text else None
    heading = card.find_previous(["h2", "h3"])
    return heading.get_text(" ", strip=True).lower().split()[0] if heading else None


def _listing_url(card: Tag, base_url: str) -> str | None:
    link = card.select_one(".listing-url a[href], a.external[href]")
    if link is None:
        return None
    href = link.get("href") or ""
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return urljoin(base_url, href)
    return href or None


def _price_tier(text: str | None) -> int | None:
    if not text:
        return None
    token = text.strip()
    if _PRICE_SYMBOL_RE.match(token):
        return len(token)
    if token.lower().startswith("free"):
        return 1
    match = _PRICE_AMOUNT_RE.search(token)
    if match:
        try:
            amount = float(match.group(1).replace(",", "."))
        except ValueError:
            return None
        # Rough euro-per-head bands for Madrid. Explicitly a heuristic.
        for ceiling, tier in ((12, 1), (30, 2), (60, 3)):
            if amount <= ceiling:
                return tier
        return 4
    return None


def _parse_hours(text: str | None) -> dict[str, Any] | None:
    """Parse free-text hours like "Mon-Sat 10:00-22:00, Sun 11:00-21:00".

    Returns None (meaning *unknown*, which the optimizer treats permissively)
    rather than an empty dict on a parse failure — claiming a place is closed
    because we could not read its hours would be worse than admitting we do not
    know.
    """
    if not text:
        return None
    out: dict[str, list[dict[str, str]]] = {k: [] for k in DAY_KEYS}
    matched = False
    for match in _HOURS_CLAUSE_RE.finditer(text):
        days_raw = match.group("days").lower().replace("–", "-")
        open_t, close_t = match.group("open"), match.group("close")
        if "-" in days_raw:
            start_name, end_name = (p.strip() for p in days_raw.split("-", 1))
            start, end = _DAY_ALIASES.get(start_name[:4]), _DAY_ALIASES.get(end_name[:4])
            if start is None or end is None:
                continue
            days = [d % 7 for d in range(start, end + 1 if end >= start else end + 8)]
        elif days_raw.startswith("daily"):
            days = list(range(7))
        else:
            day = _DAY_ALIASES.get(days_raw[:4])
            if day is None:
                continue
            days = [day]
        for day in days:
            out[DAY_KEYS[day]].append({"open": _pad(open_t), "close": _pad(close_t)})
        matched = True
    return out if matched else None


def _pad(value: str) -> str:
    hh, mm = value.split(":")
    return f"{int(hh):02d}:{mm}"
