"""Tags derived from structured data rather than from text.

Kept separate from the lexicon because the evidence is different in kind: these
come from columns we already trust (price tier, opening hours, category, the
composite score) and need no NLP, no thresholds, and no negation handling. They
are also the tags most likely to be *present*, since a place can have a price
tier and hours without anyone having written a word about it.
"""

from __future__ import annotations

from typing import Any

from app.models.enums import PlaceCategory, SourceName
from app.models.hours import intervals_for

#: price_tier -> cost tag. The UI filters on these, so the vocabulary is fixed.
PRICE_TIER_TAGS: dict[int, str] = {1: "budget", 2: "mid-range", 3: "upscale", 4: "splurge"}

#: Minute-of-day after which a closing time counts as "late night".
LATE_NIGHT_CLOSE_MINUTE = 25 * 60  # 01:00 the following day
#: Minute-of-day at/before which an opening time counts as "opens early".
EARLY_OPEN_MINUTE = 8 * 60 + 30

#: Composite score at/above which a place is tagged highly-rated.
HIGHLY_RATED_SCORE = 68.0

#: A place with strong Reddit sentiment but a small Google footprint is the
#: textbook "locals know, algorithms don't" case. The ceiling is deliberately
#: low: an earlier 8,000 ceiling tagged Salmon Guru — a World's 50 Best Bars
#: fixture with 7,400 reviews — as a hidden gem, which is absurd. A Google
#: review_count of 0 also qualifies, since being absent from Google entirely is
#: the purest form of "little known".
HIDDEN_GEM_MAX_GOOGLE_REVIEWS = 2_500
HIDDEN_GEM_MIN_REDDIT_SENTIMENT = 0.35

#: Conversely: a huge Google sample with genuinely sour local opinion is the
#: tourist-trap signature, and it is exactly the disagreement a single averaged
#: star rating would hide.
TOURIST_TRAP_MIN_GOOGLE_REVIEWS = 40_000
#: Must be meaningfully negative, not merely non-positive. A 0.0 threshold
#: treated *absent* opinion as negative and tagged Parque de El Retiro — whose
#: only Reddit comment VADER scored at exactly 0.0 — a tourist trap.
TOURIST_TRAP_MAX_REDDIT_SENTIMENT = -0.15

#: Minimum Reddit evidence before either consensus tag may fire. One neutral
#: comment is not a local consensus about anything.
MIN_REDDIT_MENTIONS_FOR_CONSENSUS = 2
MIN_REDDIT_UPVOTES_FOR_CONSENSUS = 100


def price_tags(price_tier: int | None) -> set[str]:
    if price_tier is None:
        return set()
    tag = PRICE_TIER_TAGS.get(price_tier)
    return {tag} if tag else set()


def category_tags(category: PlaceCategory) -> set[str]:
    """The category itself is a filter axis, so it is also a tag."""
    return {f"category:{category.value}"}


def hours_tags(hours: dict[str, Any] | None) -> set[str]:
    """`late-night` / `opens-early` from actual opening hours.

    Uses the weekend, because a bar closing at 02:00 on Saturday and 23:00 on
    Tuesday is still a late-night bar.
    """
    if not hours:
        return set()
    tags: set[str] = set()
    for weekday in (4, 5):  # Friday, Saturday
        for _, close in intervals_for(hours, weekday) or []:
            if close >= LATE_NIGHT_CLOSE_MINUTE:
                tags.add("late-night")
    for weekday in range(7):
        for open_min, _ in intervals_for(hours, weekday) or []:
            if open_min <= EARLY_OPEN_MINUTE:
                tags.add("opens-early")
    return tags


def consensus_tags(
    composite_score: float | None, signals_by_source: dict[SourceName, dict[str, Any]]
) -> set[str]:
    """Tags that only exist because we have *multiple* sources to compare.

    `hidden-gem` and `tourist-trap` are the payoff of the whole multi-source
    architecture: neither is visible from any single source. A place can only be
    a hidden gem if one source is enthusiastic while another has barely heard of
    it, and only a tourist trap if mass ratings and local opinion disagree.
    """
    tags: set[str] = set()
    if composite_score is not None and composite_score >= HIGHLY_RATED_SCORE:
        tags.add("highly-rated")

    google = signals_by_source.get(SourceName.GOOGLE_PLACES) or {}
    reddit = signals_by_source.get(SourceName.REDDIT) or {}
    if not reddit:
        return tags

    sentiment = float(reddit.get("sentiment") or 0.0)
    mentions = int(reddit.get("mention_count") or 0)
    upvotes = int(reddit.get("upvotes") or 0)
    # 0 review_count means either "not on Google" or "no reviews" — both count
    # as a small footprint for the hidden-gem test.
    google_reviews = int(google.get("review_count") or 0)

    has_consensus = (
        mentions >= MIN_REDDIT_MENTIONS_FOR_CONSENSUS
        or upvotes >= MIN_REDDIT_UPVOTES_FOR_CONSENSUS
    )
    if not has_consensus:
        return tags

    if (
        google_reviews <= HIDDEN_GEM_MAX_GOOGLE_REVIEWS
        and sentiment >= HIDDEN_GEM_MIN_REDDIT_SENTIMENT
    ):
        tags.add("hidden-gem")
    if (
        google_reviews >= TOURIST_TRAP_MIN_GOOGLE_REVIEWS
        and sentiment <= TOURIST_TRAP_MAX_REDDIT_SENTIMENT
    ):
        tags.add("tourist-trap")
    return tags
