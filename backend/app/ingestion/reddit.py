"""Reddit ingestion — the deliberately messy source.

**Why OAuth only.** Reddit's `robots.txt` is `Disallow: /` for every user
agent, which covers the `.json` suffix endpoints that tutorials reach for. So
this adapter does not touch them. Live mode authenticates against Reddit's
official API (`oauth.reddit.com`) with an application client id/secret via the
`client_credentials` grant — the same sanctioned path PRAW uses — and without
credentials the adapter runs on fixtures. That is a real functional limitation
of the no-key demo, and it is why Reddit credentials are the one key worth
getting: they are free and require no payment card.

(PRAW itself is not a dependency. It is a good library, but it wraps exactly
two endpoints we need behind a lazy-object model that fights the batch-ingest
shape here, and hand-rolling the two calls with `httpx` keeps the auth flow and
rate-limit handling visible rather than buried.)

**What comes out.** Unlike Google and Yelp, Reddit yields no coordinates, no
hours, no categories, and no star ratings — just names in prose. So a Reddit
`RawPlace` is a pure *signal*: it attaches to a place another source already
geocoded (see `EntityResolver._resolve_name_only`) and contributes

* `mention_count` — how many distinct comments named it
* `upvotes` — summed score of those comments
* `thread_score` — the best-performing thread it appeared in
* `sentiment` — upvote-weighted, sentence-scoped VADER compound

`rating` is deliberately left NULL. Reddit has no rating, and inventing a star
value here would launder a guess into the schema; the scoring module derives an
implied rating from sentiment instead, where the derivation is visible and
documented.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.ingestion.base import RawEvidence, RawPlace, SourceAdapter
from app.ingestion.cache import ResponseCache
from app.ingestion.mentions import MentionAggregator
from app.ingestion.normalize import synthetic_source_id
from app.models.enums import PlaceCategory, SourceName

log = logging.getLogger(__name__)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"

QUERY_TEMPLATES: tuple[str, ...] = (
    "best restaurants in {city}",
    "best tapas in {city}",
    "things to do in {city}",
    "best coffee in {city}",
    "best bars in {city}",
    "{city} hidden gems",
    "where to eat in {city}",
)

#: Threads below this score are noise.
MIN_THREAD_SCORE = 25
#: Comments below this score are noise.
MIN_COMMENT_SCORE = 2
#: Mentions required before emitting a signal. Deliberately 1: an earlier
#: version required 2 as "corroboration within Reddit", which discarded most
#: genuine recommendations (real threads name a place once, emphatically) while
#: keeping nothing useful. The real corroboration filter is downstream —
#: `EntityResolver._resolve_name_only` demands a 0.90 fuzzy match against a
#: place an authoritative source already geocoded, which no extraction artefact
#: passes. Filtering twice just loses signal.
MIN_MENTIONS = 1
#: Threads fetched per query, and comments read per thread.
THREADS_PER_QUERY = 15
COMMENT_LIMIT = 100


class RedditAdapter(SourceAdapter):
    source = SourceName.REDDIT

    def __init__(self, settings, city, *, refresh: bool = False) -> None:
        super().__init__(settings, city)
        self.refresh = refresh
        self.cache = ResponseCache(settings.cache_dir)

    def has_credentials(self) -> bool:
        return bool(self.settings.reddit_client_id and self.settings.reddit_client_secret)

    # --- live ---------------------------------------------------------------

    def _access_token(self, client: httpx.Client) -> str:
        creds = f"{self.settings.reddit_client_id}:{self.settings.reddit_client_secret}"
        basic = base64.b64encode(creds.encode()).decode()
        response = client.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            headers={
                "Authorization": f"Basic {basic}",
                "User-Agent": self.settings.reddit_user_agent,
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def fetch_live(self) -> list[RawPlace]:
        with httpx.Client(timeout=25.0) as client:
            token = self._access_token(client)
            headers = {
                "Authorization": f"bearer {token}",
                "User-Agent": self.settings.reddit_user_agent,
            }
            threads = self._collect_threads(client, headers)
            documents = self._collect_documents(client, headers, threads)
        return self._aggregate(documents)

    def _collect_threads(
        self, client: httpx.Client, headers: dict[str, str]
    ) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for subreddit in self.city.subreddits:
            for template in QUERY_TEMPLATES:
                query = template.format(city=self.city.name)
                cache_key = f"{self.city.slug}:search:{subreddit}:{query}"
                payload = None if self.refresh else self.cache.get("reddit", cache_key)
                if payload is None:
                    try:
                        response = client.get(
                            f"{API_BASE}/r/{subreddit}/search",
                            params={
                                "q": query,
                                "restrict_sr": "on",
                                "sort": "top",
                                "t": "year",
                                "limit": THREADS_PER_QUERY,
                            },
                            headers=headers,
                        )
                        response.raise_for_status()
                        payload = response.json()
                        self.cache.put("reddit", cache_key, payload)
                    except httpx.HTTPError as exc:
                        log.warning("reddit search failed (%s / %s): %s", subreddit, query, exc)
                        continue
                for child in (payload.get("data") or {}).get("children") or []:
                    data = child.get("data") or {}
                    if (data.get("score") or 0) < MIN_THREAD_SCORE:
                        continue
                    tid = data.get("id")
                    if tid and tid not in seen:
                        seen[tid] = data
        log.info("reddit: %d threads above score %d", len(seen), MIN_THREAD_SCORE)
        return list(seen.values())

    def _collect_documents(
        self, client: httpx.Client, headers: dict[str, str], threads: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = []
        for thread in threads:
            docs.extend(_thread_documents(thread))
            tid = thread.get("id")
            cache_key = f"{self.city.slug}:comments:{tid}"
            payload = None if self.refresh else self.cache.get("reddit", cache_key)
            if payload is None:
                try:
                    response = client.get(
                        f"{API_BASE}/comments/{tid}",
                        params={"limit": COMMENT_LIMIT, "depth": 2, "sort": "top"},
                        headers=headers,
                    )
                    response.raise_for_status()
                    payload = response.json()
                    self.cache.put("reddit", cache_key, payload)
                except httpx.HTTPError as exc:
                    log.warning("reddit comments failed (%s): %s", tid, exc)
                    continue
            docs.extend(_comment_documents(payload, thread))
        return docs

    # --- fixture ------------------------------------------------------------

    def fetch_fixture(self) -> list[RawPlace]:
        path = FIXTURE_DIR / f"reddit_{self.city.slug}.json"
        if not path.exists():
            log.warning("no reddit fixture for %s", self.city.slug)
            return []
        data = json.loads(path.read_text("utf-8"))
        docs: list[dict[str, Any]] = []
        for entry in data.get("threads", []):
            thread = entry["thread"]
            if (thread.get("score") or 0) < MIN_THREAD_SCORE:
                continue
            docs.extend(_thread_documents(thread))
            docs.extend(_comment_documents(entry["comments"], thread))
        return self._aggregate(docs)

    # --- shared aggregation -------------------------------------------------

    def _aggregate(self, documents: list[dict[str, Any]]) -> list[RawPlace]:
        """Turn raw text documents into per-place Reddit signals.

        Both the live and fixture paths funnel through here, so mock mode
        exercises the real extraction, sentiment and aggregation code.
        """
        aggregator = MentionAggregator()
        for doc in documents:
            aggregator.add_document(
                doc["text"],
                upvotes=doc.get("score") or 0,
                thread_score=doc.get("thread_score") or 0,
                url=doc.get("url"),
                subreddit=doc.get("subreddit"),
                thread_id=doc.get("thread_id"),
                created_utc=doc.get("created_utc"),
            )

        out: list[RawPlace] = []
        for mention in aggregator.mentions(min_mentions=MIN_MENTIONS):
            observed = (
                datetime.fromtimestamp(mention.newest_created_utc, tz=UTC)
                if mention.newest_created_utc
                else None
            )
            out.append(
                RawPlace(
                    source=self.source,
                    # Stable across runs: the same place keeps the same signal
                    # row instead of accumulating duplicates.
                    source_place_id=synthetic_source_id("reddit", self.city.slug, mention.key),
                    name=mention.display_name,
                    city=self.city.name,
                    category=PlaceCategory.OTHER,  # Reddit tells us nothing here
                    lat=None,
                    lng=None,
                    rating=None,  # see module docstring: no rating is honest
                    raw_rating=None,
                    review_count=mention.mention_count,
                    url=next((u for _, u, _ in mention.snippets if u), None),
                    extra={
                        "mention_count": mention.mention_count,
                        "upvotes": mention.upvotes,
                        "thread_score": mention.thread_score,
                        "sentiment": round(mention.weighted_sentiment, 4),
                        "subreddits": sorted(mention.subreddits),
                        "thread_count": len(mention.thread_ids),
                    },
                    observed_at=observed,
                    evidence=[
                        RawEvidence(text=text, url=url, weight=weight)
                        for text, url, weight in mention.snippets
                    ],
                )
            )
        log.info("reddit: %d place signals from %d documents", len(out), len(documents))
        return out


# --- document shaping (shared by live + fixture) ----------------------------


def _thread_documents(thread: dict[str, Any]) -> list[dict[str, Any]]:
    """The thread's own title and body as scoreable documents."""
    permalink = thread.get("permalink") or ""
    url = f"https://www.reddit.com{permalink}" if permalink else None
    base = {
        "score": thread.get("score") or 0,
        "thread_score": thread.get("score") or 0,
        "url": url,
        "subreddit": thread.get("subreddit"),
        "thread_id": thread.get("id"),
        "created_utc": thread.get("created_utc"),
    }
    docs = []
    for field in ("title", "selftext"):
        text = (thread.get(field) or "").strip()
        if text:
            docs.append({**base, "text": text})
    return docs


def _comment_documents(payload: Any, thread: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten Reddit's nested comment listing.

    `/comments/{id}` returns a two-element array: [post listing, comment
    listing]. Comments nest arbitrarily via `replies`, which is either another
    Listing or the empty string `""` at the leaves — that inconsistency is why
    this walks explicitly instead of recursing on a type assumption.
    """
    listing = payload[1] if isinstance(payload, list) and len(payload) > 1 else payload
    thread_score = thread.get("score") or 0
    subreddit = thread.get("subreddit")
    thread_id = thread.get("id")
    docs: list[dict[str, Any]] = []

    stack = list(((listing or {}).get("data") or {}).get("children") or [])
    while stack:
        child = stack.pop()
        if child.get("kind") != "t1":
            continue
        data = child.get("data") or {}
        body = (data.get("body") or "").strip()
        score = data.get("score") or 0
        if body and body not in ("[deleted]", "[removed]") and score >= MIN_COMMENT_SCORE:
            permalink = data.get("permalink") or ""
            docs.append(
                {
                    "text": body,
                    "score": score,
                    "thread_score": thread_score,
                    "url": f"https://www.reddit.com{permalink}" if permalink else None,
                    "subreddit": subreddit,
                    "thread_id": thread_id,
                    "created_utc": data.get("created_utc"),
                }
            )
        replies = data.get("replies")
        if isinstance(replies, dict):
            stack.extend((replies.get("data") or {}).get("children") or [])
    return docs
