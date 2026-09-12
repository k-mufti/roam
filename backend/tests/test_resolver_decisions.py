"""Entity-resolution decision tests.

These are the tests that matter most in this project: a dedup pipeline that
silently over-merges destroys data, and one that under-merges destroys the whole
premise of cross-source corroboration. Every case below is drawn from real
Madrid data that the resolver got wrong at some point during development.

No database: `matching_rule` / `merge_confidence` are pure functions of
(name similarity, distance, anchor), which is exactly why they were factored out
of the persistence path.
"""

from __future__ import annotations

import pytest

from app.ingestion.normalize import distinctive_tokens, normalize_name
from app.ingestion.resolver import (
    MAX_MERGE_DISTANCE_M,
    REVIEW_CONFIDENCE,
    REVIEW_MIN_NAME_SIMILARITY,
    matching_rule,
    merge_confidence,
    name_similarity,
    proximity_score,
)


def decide(left: str, right: str, distance_m: float) -> str:
    """Mirror the resolver's decision for a candidate pair."""
    a, b = normalize_name(left), normalize_name(right)
    sim = name_similarity(a, b)
    anchored = bool(distinctive_tokens(a) & distinctive_tokens(b))
    if matching_rule(sim, distance_m, anchored) is not None:
        return "MERGE"
    flagged = (
        merge_confidence(sim, distance_m, anchored) >= REVIEW_CONFIDENCE
        and sim >= REVIEW_MIN_NAME_SIMILARITY
    )
    return "FLAG" if flagged else "CREATE"


#: (google/yelp name, other name, metres, expected decision, why)
CASES = [
    # The spec's own example: same place, extra neighbourhood token.
    ("Café Rivas", "Cafe Rivas Palermo", 30, "MERGE", "accents + extra token"),
    # Cross-language: only ~0.67 fuzzy similarity, rescued by the shared anchor.
    ("Retiro Park", "Parque de El Retiro", 14, "MERGE", "cross-language"),
    ("Museo del Prado", "Museo Nacional del Prado", 15, "MERGE", "official vs colloquial"),
    ("Reina Sofia Museum", "Museo Nacional Centro de Arte Reina Sofía", 15, "MERGE", "abbrev"),
    ("Botín", "Sobrino de Botín", 14, "MERGE", "partial official name"),
    ("Mercado San Miguel", "Mercado de San Miguel", 10, "MERGE", "dropped article"),
    # Must NOT merge: distinct places sharing only a generic token.
    ("Casa Lucio", "Casa Botín", 40, "FLAG", "generic 'casa' is not an anchor"),
    ("Casa Dani", "Mercado de la Paz", 15, "CREATE", "a stall inside a market is not the market"),
    # Must NOT merge: identical name, different branch across town.
    ("Toma Café", "Toma Café", 800, "CREATE", "beyond max merge distance"),
    # Same-name places just inside the distance cap need near-identical names.
    ("Toma Café", "Toma Café", 240, "MERGE", "same name within cap"),
]


@pytest.mark.parametrize("left,right,dist,expected,why", CASES)
def test_merge_decisions(left, right, dist, expected, why):
    assert decide(left, right, dist) == expected, f"{left!r} vs {right!r} ({why})"


class TestProximityScore:
    def test_monotonically_decreasing(self):
        scores = [proximity_score(d) for d in (0, 15, 40, 90, 150, 250)]
        assert scores == sorted(scores, reverse=True)

    def test_half_distance(self):
        assert proximity_score(90.0) == pytest.approx(0.5)

    def test_short_range_is_near_certain(self):
        assert proximity_score(15.0) > 0.95


class TestMergeConfidence:
    def test_beyond_max_distance_is_zero(self):
        assert merge_confidence(1.0, MAX_MERGE_DISTANCE_M + 1) == 0.0

    def test_anchor_adds_confidence(self):
        assert merge_confidence(0.8, 50, anchored=True) > merge_confidence(0.8, 50, anchored=False)

    def test_bounded(self):
        assert 0.0 <= merge_confidence(1.0, 0.0, anchored=True) <= 1.0


class TestNameSimilarity:
    def test_identical(self):
        assert name_similarity("casa lucio", "casa lucio") == pytest.approx(1.0)

    def test_empty_is_zero(self):
        assert name_similarity("", "casa lucio") == 0.0

    def test_extra_token_tolerated(self):
        assert name_similarity("cafe rivas", "cafe rivas palermo") > 0.8

    def test_shared_generic_word_is_not_enough(self):
        # The failure mode that motivated blending a strict ratio in.
        assert name_similarity("casa lucio", "casa botin") < 0.75


def test_review_queue_requires_confusable_names():
    """Proximity alone must not enqueue a pair for human review: at 15m in a
    dense centre that would be nearly every pair."""
    assert decide("Casa Dani", "Mercado de la Paz", 15) == "CREATE"
    assert decide("Casa Lucio", "Casa Botín", 40) == "FLAG"


def test_tier_one_requires_anchor():
    """Distance alone must never authorize a merge: food-hall stalls are metres
    apart and would otherwise collapse into one record."""
    sim = name_similarity("casa dani", "casa pepe")
    assert matching_rule(sim, 5.0, anchored=False) is None
