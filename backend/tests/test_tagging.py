"""Tagging pipeline tests.

The negation cases are the important ones: naive substring matching gets all of
them backwards, and every string below is real review text from the fixtures.
"""

from __future__ import annotations

import pytest

from app.models.enums import PlaceCategory, SourceName
from app.models.hours import from_google_periods
from app.tagging import structural
from app.tagging.keywords import TOPIC_PREFIX, extract_keywords, lexicon_vocabulary
from app.tagging.lexicon import matches_any, phrase_hits
from app.tagging.pipeline import Snippet, tags_from_text


def snip(text, weight=1.0, sentiment=0.4):
    return Snippet(text=text, weight=weight, sentiment=sentiment)


class TestNegation:
    """A match preceded by a negator must not fire the tag."""

    @pytest.mark.parametrize(
        "text,phrase",
        [
            ("Tiny, no laptops, in and out.", "laptop"),
            ("Loud, fun, not a place for a quiet dinner.", "quiet"),
            ("Wild flavours, deafening music. Not a romantic dinner.", "romantic"),
            ("A real neighbourhood market, not a tourist one.", "tourist"),
            ("It's not cheap, honestly.", "cheap"),
        ],
    )
    def test_negated_phrases_do_not_match(self, text, phrase):
        assert phrase_hits(text, phrase) == 0

    @pytest.mark.parametrize(
        "text,phrase",
        [
            ("Laptop friendly and plenty of plugs.", "laptop"),
            ("Quiet, dim, no music. Feels frozen in 1935.", "quiet"),
            ("It's not cheap, but the quiet terrace is lovely.", "quiet"),
            ("Touristy but genuinely good.", "touristy"),
        ],
    )
    def test_unnegated_phrases_match(self, text, phrase):
        assert phrase_hits(text, phrase) >= 1

    def test_clause_boundary_blocks_negation(self):
        """Without the boundary guard, a 6-token window would let "No music"
        negate the "quiet" in the previous sentence."""
        assert phrase_hits("It's quiet. No music.", "quiet") == 1

    def test_token_matching_not_substring(self):
        assert phrase_hits("Book online for updates", "line") == 0
        assert phrase_hits("The queue was 40 minutes", "queue") == 1

    def test_vetoes_match_literally(self):
        """Veto phrases ARE negations, so they must bypass the negation filter
        or they would cancel themselves out."""
        assert matches_any("Tiny, no laptops, in and out.", ("no laptops",))


class TestLexiconTagging:
    def test_laptop_veto_beats_mentions(self):
        result = tags_from_text(
            [
                snip("Great wifi and plenty of plugs, I work here often."),
                snip("Tiny, no laptops, in and out."),
            ]
        )
        assert "laptop-friendly" not in result.tags

    def test_laptop_friendly_without_veto(self):
        result = tags_from_text(
            [snip("Laptop friendly, plenty of plugs, I worked here solo for three hours.")]
        )
        assert "laptop-friendly" in result.tags

    def test_evidence_share_threshold(self):
        """One stray mention among many snippets must not carry a tag."""
        snippets = [snip("The tortilla is excellent.") for _ in range(20)]
        snippets.append(snip("Good for kids too."))
        assert "family-friendly" not in tags_from_text(snippets).tags

    def test_upvote_weight_lets_one_strong_comment_carry_a_tag(self):
        result = tags_from_text(
            [
                snip("Quiet, dim, proper classic cocktails.", weight=40.0),
                snip("The tortilla is fine.", weight=1.0),
            ]
        )
        assert "quiet" in result.tags

    def test_sentiment_floor(self):
        """date-night requires positive sentiment, so a sour 'date' mention
        must not tag the place."""
        result = tags_from_text(
            [snip("Loud, cramped, cheap. Terrible for a date.", sentiment=-0.7)]
        )
        assert "date-night" not in result.tags

    def test_no_snippets_yields_no_tags(self):
        assert tags_from_text([]).tags == []

    def test_evidence_is_returned_for_auditing(self):
        result = tags_from_text([snip("Cheap vermouth, standing room only, full of locals.")])
        assert result.evidence
        for item in result.evidence:
            assert item.matched_phrases
            assert 0.0 <= item.share <= 1.0


class TestStructuralTags:
    @pytest.mark.parametrize(
        "tier,expected", [(1, "budget"), (2, "mid-range"), (3, "upscale"), (4, "splurge")]
    )
    def test_price_tiers(self, tier, expected):
        assert structural.price_tags(tier) == {expected}

    def test_missing_price_yields_nothing(self):
        assert structural.price_tags(None) == set()

    def test_category_tag_is_namespaced(self):
        assert structural.category_tags(PlaceCategory.BAR) == {"category:bar"}

    def test_late_night_from_overnight_hours(self):
        bar = from_google_periods(
            [
                {"open": {"day": d, "hour": 19, "minute": 0},
                 "close": {"day": (d + 1) % 7, "hour": 2, "minute": 0}}
                for d in range(7)
            ]
        )
        assert "late-night" in structural.hours_tags(bar)

    def test_opens_early_from_morning_hours(self):
        cafe = from_google_periods(
            [
                {"open": {"day": d, "hour": 8, "minute": 0},
                 "close": {"day": d, "hour": 21, "minute": 0}}
                for d in range(7)
            ]
        )
        assert "opens-early" in structural.hours_tags(cafe)

    def test_unknown_hours_yield_nothing(self):
        assert structural.hours_tags(None) == set()


class TestConsensusTags:
    """These tags are the payoff of multi-source ingestion: neither is
    computable from any single source."""

    def test_neutral_sentiment_is_not_negative(self):
        """The bug this guards: a 0.0 sentiment meant 'no opinion found', and a
        <= 0.0 threshold read it as a bad review, tagging Parque de El Retiro
        (152k Google reviews, one neutral comment) a tourist trap."""
        tags = structural.consensus_tags(
            76.0,
            {
                SourceName.GOOGLE_PLACES: {"review_count": 152_003},
                SourceName.REDDIT: {"sentiment": 0.0, "mention_count": 3, "upvotes": 844},
            },
        )
        assert "tourist-trap" not in tags

    def test_genuinely_sour_local_opinion_is_a_tourist_trap(self):
        tags = structural.consensus_tags(
            60.0,
            {
                SourceName.GOOGLE_PLACES: {"review_count": 80_000},
                SourceName.REDDIT: {"sentiment": -0.45, "mention_count": 4, "upvotes": 900},
            },
        )
        assert "tourist-trap" in tags

    def test_famous_place_is_not_a_hidden_gem(self):
        """Salmon Guru has 7,400 Google reviews and is on world bar lists; an
        8,000-review ceiling tagged it a hidden gem."""
        tags = structural.consensus_tags(
            67.0,
            {
                SourceName.GOOGLE_PLACES: {"review_count": 7_420},
                SourceName.REDDIT: {"sentiment": 0.40, "mention_count": 2, "upvotes": 498},
            },
        )
        assert "hidden-gem" not in tags

    def test_absent_from_google_plus_loved_on_reddit_is_a_hidden_gem(self):
        tags = structural.consensus_tags(
            68.0,
            {SourceName.REDDIT: {"sentiment": 0.55, "mention_count": 2, "upvotes": 743}},
        )
        assert "hidden-gem" in tags

    def test_single_weak_mention_is_not_a_consensus(self):
        tags = structural.consensus_tags(
            68.0,
            {SourceName.REDDIT: {"sentiment": 0.9, "mention_count": 1, "upvotes": 3}},
        )
        assert "hidden-gem" not in tags

    def test_no_reddit_signal_means_no_consensus_tags(self):
        tags = structural.consensus_tags(
            90.0, {SourceName.GOOGLE_PLACES: {"review_count": 100_000}}
        )
        assert tags == {"highly-rated"}


class TestKeywords:
    def test_curated_vocabulary_is_excluded(self):
        """A place already tagged `quiet` gaining `topic:quiet` is pure noise."""
        vocab = lexicon_vocabulary()
        assert "quiet" in vocab and "laptop" in vocab and "hidden-gem" in vocab

    def test_distinctive_terms_beat_ubiquitous_ones(self):
        docs = {
            "a": "sherry sherry sherry tapas tapas madrid",
            "b": "churros churros churros tapas tapas madrid",
            "c": "tortilla tortilla tortilla tapas tapas madrid",
            "d": "sherry churros tortilla tapas madrid",
        }
        out = extract_keywords(docs)
        # "tapas" and "madrid" appear everywhere -> no discriminating power.
        for terms in out.values():
            assert f"{TOPIC_PREFIX}tapas" not in terms
            assert f"{TOPIC_PREFIX}madrid" not in terms

    def test_place_name_tokens_can_be_vetoed(self):
        docs = {
            "a": "retiro retiro retiro rowboat rowboat lake",
            "b": "retiro prado prado prado museum",
            "c": "retiro rowboat prado gallery gallery",
        }
        out = extract_keywords(docs, exclude_terms={"a": {"retiro"}})
        assert f"{TOPIC_PREFIX}retiro" not in out.get("a", [])

    def test_tiny_corpus_is_skipped_not_crashed(self):
        assert extract_keywords({"a": "one document only"}) == {}

    def test_empty_documents_are_ignored(self):
        assert extract_keywords({"a": "", "b": "   ", "c": ""}) == {}
