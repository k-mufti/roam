"""Text and geo normalization shared by every ingestion adapter.

Everything here is deliberately dependency-light and pure, so the entity
resolver's behaviour is unit-testable without a database.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata

from app.models.enums import PlaceCategory

#: Generic words that sources bolt onto names and that carry no identity.
#: "Restaurante Botín" (Google) vs "Botín" (Yelp) vs "Sobrino de Botín"
#: (Reddit) should all collapse toward the same key.
#:
#: This list is intentionally short. Aggressive stripping is how fuzzy matchers
#: start merging distinct places: in Madrid, "Casa", "Taberna" and "Bodega" are
#: load-bearing parts of real names ("Casa Lucio", "Taberna La Bola"), so they
#: are NOT here.
NOISE_TOKENS = frozenset(
    {
        "restaurante", "restaurant", "restaurante&bar", "cafeteria",
        "coffee", "coffeehouse", "shop", "museum", "museo",
        "hotel", "hostal", "the", "el", "la", "los", "las", "de", "del",
        "y", "and", "of", "&",
    }
)

#: Tokens that are real parts of a name but carry almost no *identifying*
#: power, because dozens of Madrid places share them. A shared token from this
#: set is NOT evidence that two records are the same place: "Casa Lucio" and
#: "Casa Botín" both contain "casa" and are 40m apart, yet are unrelated.
#:
#: Distinct from NOISE_TOKENS, which are stripped from the matching key
#: entirely. These are *kept* in the key (they help fuzzy similarity) but are
#: excluded from the distinctive-token test in the resolver.
GENERIC_NAME_TOKENS = frozenset(
    {
        "casa", "taberna", "tasca", "bodega", "bar", "cafe", "café",
        "mercado", "market", "plaza", "square", "parque", "park", "jardin",
        "jardín", "garden", "palacio", "palace", "teatro", "theatre", "theater",
        "calle", "puerta", "gate", "centro", "centre", "center", "nacional",
        "national", "real", "royal", "arte", "art", "madrid", "espana",
        "españa", "spain", "bistro",
        "brasserie", "club", "house", "grande", "gran", "nuevo", "nueva",
        "viejo", "vieja", "antigua", "antiguo",
    }
)

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

EARTH_RADIUS_M = 6_371_000.0


def strip_accents(value: str) -> str:
    """'Café Rivas' -> 'Cafe Rivas'. Necessary because sources disagree about
    Spanish diacritics constantly, and a bare string compare would treat
    'Botín' and 'Botin' as different places."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(name: str) -> str:
    """Produce the matching key stored in `Place.name_normalized`.

    Lowercase, de-accented, de-punctuated, and stripped of generic noise words
    — but only when at least two meaningful tokens survive, so a place
    genuinely called "El Museo" does not normalize to the empty string.
    """
    base = _PUNCT_RE.sub(" ", strip_accents(name).lower())
    tokens = [t for t in _WS_RE.split(base) if t]
    meaningful = [t for t in tokens if t not in NOISE_TOKENS]
    if len(meaningful) >= 2:
        tokens = meaningful
    elif meaningful:
        # One meaningful token left: keep it alone ("Restaurante Botín" -> "botin").
        tokens = meaningful
    return " ".join(tokens).strip()


def distinctive_tokens(normalized_name: str) -> frozenset[str]:
    """Tokens from a normalized name that actually identify a place.

    Used by entity resolution as an *anchor*: if two records share one of
    these, a plausible-but-imperfect fuzzy score becomes much more believable.
    Short tokens are dropped because 3-letter fragments collide constantly.
    """
    return frozenset(
        t for t in normalized_name.split() if len(t) >= 4 and t not in GENERIC_NAME_TOKENS
    )


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres.

    Used for in-memory candidate scoring. The *database* uses PostGIS
    ST_DWithin on a geography column for the actual radius filter; this exists
    so resolver logic can be tested without a live Postgres.
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def content_hash(text: str) -> str:
    """Stable 64-char digest used to deduplicate text evidence across re-runs."""
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def synthetic_source_id(source: str, *parts: str) -> str:
    """Deterministic identifier for sources that have no native place id.

    Reddit and blog mentions are just names in prose. Hashing the source plus
    the stable parts (thread id, matched name) means re-ingesting the same
    thread updates the existing signal instead of duplicating it.
    """
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{source}:{digest}"


#: Google Place `types` -> our category. Order matters: first match wins, so
#: the more specific types are listed before the generic ones.
GOOGLE_TYPE_MAP: tuple[tuple[str, PlaceCategory], ...] = (
    ("night_club", PlaceCategory.NIGHTLIFE),
    ("bar", PlaceCategory.BAR),
    ("cafe", PlaceCategory.CAFE),
    ("coffee_shop", PlaceCategory.CAFE),
    ("bakery", PlaceCategory.CAFE),
    ("museum", PlaceCategory.MUSEUM),
    ("art_gallery", PlaceCategory.MUSEUM),
    ("park", PlaceCategory.PARK),
    ("zoo", PlaceCategory.ATTRACTION),
    ("tourist_attraction", PlaceCategory.ATTRACTION),
    ("historical_landmark", PlaceCategory.ATTRACTION),
    ("church", PlaceCategory.ATTRACTION),
    ("stadium", PlaceCategory.ATTRACTION),
    ("food_court", PlaceCategory.RESTAURANT),
    # A Madrid "mercado" is a food hall you eat in, so it belongs in the
    # itinerary as a meal stop, not as a shop.
    ("market", PlaceCategory.RESTAURANT),
    ("shopping_mall", PlaceCategory.SHOPPING),
    ("store", PlaceCategory.SHOPPING),
    ("lodging", PlaceCategory.HOTEL),
    ("hotel", PlaceCategory.HOTEL),
    ("restaurant", PlaceCategory.RESTAURANT),
    ("food", PlaceCategory.RESTAURANT),
)

#: Yelp alias fragments -> our category, same first-match-wins semantics.
YELP_ALIAS_MAP: tuple[tuple[str, PlaceCategory], ...] = (
    ("nightlife", PlaceCategory.NIGHTLIFE),
    ("danceclub", PlaceCategory.NIGHTLIFE),
    ("cocktailbar", PlaceCategory.BAR),
    ("wine_bar", PlaceCategory.BAR),
    ("bars", PlaceCategory.BAR),
    ("pubs", PlaceCategory.BAR),
    ("coffee", PlaceCategory.CAFE),
    ("cafes", PlaceCategory.CAFE),
    ("bakeries", PlaceCategory.CAFE),
    ("museums", PlaceCategory.MUSEUM),
    ("parks", PlaceCategory.PARK),
    ("landmarks", PlaceCategory.ATTRACTION),
    ("tours", PlaceCategory.ATTRACTION),
    ("hotels", PlaceCategory.HOTEL),
    ("shopping", PlaceCategory.SHOPPING),
    ("restaurants", PlaceCategory.RESTAURANT),
    ("food", PlaceCategory.RESTAURANT),
)


def map_category(
    values: list[str],
    table: tuple[tuple[str, PlaceCategory], ...],
    primary: str | None = None,
) -> PlaceCategory:
    """Collapse a source's own taxonomy into our normalized `PlaceCategory`.

    `primary` gets its own first pass. Sources that designate a primary type
    (Google's `primaryType`) are telling us which of a place's many types is
    the identifying one, and that beats table order. Without this,
    "Mercado de San Miguel" — types `[market, tourist_attraction, food]` —
    resolves to ATTRACTION purely because `tourist_attraction` happens to sit
    earlier in the table.
    """
    if primary:
        needle_lower = primary.lower()
        for needle, category in table:
            if needle in needle_lower:
                return category
    lowered = [v.lower() for v in values]
    for needle, category in table:
        if any(needle in v for v in lowered):
            return category
    return PlaceCategory.OTHER
