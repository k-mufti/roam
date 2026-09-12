"""Scoring model tests.

The model is a pure function of (signals, distributions, now), so every property
below is tested without a database. Each test names the modelling claim it
defends — these are the assertions that justify "not a raw average".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import SourceName
from app.scoring import config as C
from app.scoring.model import (
    SignalInput,
    SourceDistribution,
    corroboration,
    distribution_for,
    recency_weight,
    score_place,
    score_signal,
    shrink_rating,
)

NOW = datetime(2026, 9, 12, tzinfo=UTC)

# Realistic measured distributions: Google is tight and inflated, Yelp is wide.
DISTS = {
    SourceName.GOOGLE_PLACES: SourceDistribution(4.48, 0.16, 37, measured=True),
    SourceName.YELP: SourceDistribution(4.26, 0.47, 23, measured=True),
}


def google(rating, n, days_old=0):
    return SignalInput(
        SourceName.GOOGLE_PLACES, rating, n, NOW - timedelta(days=days_old)
    )


def yelp(rating, n, days_old=0):
    return SignalInput(SourceName.YELP, rating, n, NOW - timedelta(days=days_old))


def reddit(sentiment, upvotes, mentions, thread=500, days_old=30):
    return SignalInput(
        SourceName.REDDIT,
        None,
        mentions,
        NOW - timedelta(days=days_old),
        {
            "sentiment": sentiment,
            "upvotes": upvotes,
            "mention_count": mentions,
            "thread_score": thread,
        },
    )


def blog(days_old=0):
    return SignalInput(SourceName.BLOG, None, None, NOW - timedelta(days=days_old),
                       {"section": "eat", "editorially_listed": True})


class TestNotARawAverage:
    def test_review_volume_breaks_the_tie(self):
        """4.5 from 900 reviewers must outrank 4.5 from 3."""
        many = score_place([google(4.5, 900)], DISTS, NOW)
        few = score_place([google(4.5, 3)], DISTS, NOW)
        assert many.composite_score > few.composite_score

    def test_thin_sample_regresses_toward_the_source_mean(self):
        shrunk = shrink_rating(4.9, 3, prior_mean=4.48)
        assert 4.48 < shrunk < 4.6  # barely moved off the mean

    def test_large_sample_keeps_its_own_rating(self):
        shrunk = shrink_rating(4.9, 50_000, prior_mean=4.48)
        assert shrunk == pytest.approx(4.9, abs=0.01)

    def test_same_number_means_different_things_per_source(self):
        """4.3 is below Google's mean (4.48) but above Yelp's (4.26).

        This is the central claim: averaging the two raw numbers would treat
        them as the same statement about quality.
        """
        g = score_signal(google(4.3, 500), DISTS, NOW)
        y = score_signal(yelp(4.3, 500), DISTS, NOW)
        assert g.z_score < 0 < y.z_score
        assert y.quality > g.quality


class TestCorroboration:
    def test_agreeing_sources_beat_one_source(self):
        alone = score_place([google(4.6, 800)], DISTS, NOW)
        together = score_place([google(4.6, 800), yelp(4.7, 400)], DISTS, NOW)
        assert together.composite_score > alone.composite_score
        assert together.corroboration_bonus > 0

    def test_single_source_gets_no_bonus(self):
        result = score_place([google(4.6, 800)], DISTS, NOW)
        assert result.corroboration_bonus == 0.0
        assert result.source_count == 1

    def test_disagreement_earns_less_than_agreement(self):
        agree = score_place([google(4.7, 800), yelp(4.8, 400)], DISTS, NOW)
        disagree = score_place([google(4.7, 800), yelp(3.0, 400)], DISTS, NOW)
        assert agree.corroboration_bonus > disagree.corroboration_bonus
        assert agree.agreement > disagree.agreement

    def test_bonus_is_capped(self):
        many = score_place(
            [google(4.9, 9000), yelp(4.9, 900), reddit(0.9, 3000, 8), blog()], DISTS, NOW
        )
        assert many.corroboration_bonus <= C.CORROBORATION_MAX_BONUS

    def test_diminishing_returns(self):
        two = corroboration(
            [score_signal(google(4.6, 800), DISTS, NOW), score_signal(yelp(4.6, 800), DISTS, NOW)]
        )[1]
        # (1 - 1/2) = 0.5 of the available bonus at two sources.
        assert two <= C.CORROBORATION_MAX_BONUS * 0.5 + 1e-9


class TestEvidenceConfidence:
    def test_lone_weak_source_regresses_to_neutral(self):
        """The bug this fixes: a weighted mean is scale-invariant, so one blog
        listing used to score identically to one 87k-review Google rating."""
        only_blog = score_place([blog()], DISTS, NOW)
        only_google = score_place([google(4.8, 87_000)], DISTS, NOW)
        assert only_blog.composite_score < only_google.composite_score
        # ...and lands just above the neutral midpoint, not at its raw quality.
        assert 50 < only_blog.composite_score < 60
        assert only_blog.raw_base == pytest.approx(C.BLOG_INCLUSION_QUALITY)

    def test_confidence_rises_with_more_evidence(self):
        one = score_place([google(4.6, 800)], DISTS, NOW)
        three = score_place([google(4.6, 800), yelp(4.6, 400), reddit(0.5, 900, 4)], DISTS, NOW)
        assert three.evidence_confidence > one.evidence_confidence

    def test_a_bad_place_is_not_rescued_by_corroboration(self):
        """The bonus is additive and capped so it reorders comparable places
        without letting a poorly-rated place outrank an excellent one."""
        bad_everywhere = score_place(
            [google(3.4, 900), yelp(2.9, 500), reddit(-0.6, 800, 5), blog()], DISTS, NOW
        )
        great_alone = score_place([google(4.9, 20_000)], DISTS, NOW)
        assert bad_everywhere.composite_score < great_alone.composite_score


class TestRecency:
    def test_older_evidence_weighs_less(self):
        fresh = score_signal(google(4.6, 800, days_old=0), DISTS, NOW)
        stale = score_signal(google(4.6, 800, days_old=1200), DISTS, NOW)
        assert stale.weight < fresh.weight

    def test_half_life(self):
        w, age = recency_weight(NOW - timedelta(days=C.RECENCY_HALF_LIFE_DAYS), NOW)
        assert w == pytest.approx(0.5, abs=0.01)
        assert age == pytest.approx(C.RECENCY_HALF_LIFE_DAYS, abs=0.1)

    def test_never_fully_decays(self):
        w, _ = recency_weight(NOW - timedelta(days=40_000), NOW)
        assert w == C.MIN_RECENCY_WEIGHT

    def test_unknown_age_is_neither_rewarded_nor_punished(self):
        w, age = recency_weight(None, NOW)
        assert age is None
        assert 0.5 < w < 1.0


class TestReddit:
    def test_rating_stays_none(self):
        """Reddit has no stars; the model must derive quality, not invent a
        rating and persist it as if the source had provided one."""
        s = score_signal(reddit(0.8, 900, 5), DISTS, NOW)
        assert s.raw_rating is None and s.z_score is None

    def test_positive_sentiment_beats_negative(self):
        pos = score_signal(reddit(0.8, 900, 5), DISTS, NOW)
        neg = score_signal(reddit(-0.8, 900, 5), DISTS, NOW)
        assert pos.quality > 0.5 > neg.quality

    def test_thin_evidence_shrinks_sentiment_toward_neutral(self):
        loud = score_signal(reddit(0.9, 2000, 6), DISTS, NOW)
        whisper = score_signal(reddit(0.9, 2, 1), DISTS, NOW)
        assert loud.quality > whisper.quality
        assert abs(whisper.quality - 0.5) < abs(loud.quality - 0.5)

    def test_upvotes_drive_weight_not_just_mention_count(self):
        """Per spec: weight by thread upvotes and sentiment, not mention count."""
        upvoted_once = score_signal(reddit(0.5, 3000, 1), DISTS, NOW)
        ignored_often = score_signal(reddit(0.5, 3, 3), DISTS, NOW)
        assert upvoted_once.weight > ignored_often.weight

    def test_thread_visibility_contributes(self):
        big = score_signal(reddit(0.5, 500, 3, thread=3000), DISTS, NOW)
        small = score_signal(reddit(0.5, 500, 3, thread=20), DISTS, NOW)
        assert big.weight > small.weight

    def test_quality_stays_in_range(self):
        for sentiment in (-1.0, -0.3, 0.0, 0.4, 1.0):
            s = score_signal(reddit(sentiment, 5000, 20), DISTS, NOW)
            assert 0.0 <= s.quality <= 1.0


class TestDistributionMeasurement:
    def test_thin_data_uses_fallback(self):
        d = distribution_for(SourceName.GOOGLE_PLACES, [4.5, 4.6])
        assert not d.measured
        assert d.mean == C.FALLBACK_DISTRIBUTION[SourceName.GOOGLE_PLACES][0]

    def test_enough_data_is_measured(self):
        d = distribution_for(SourceName.YELP, [4.0, 4.5, 3.5, 4.2, 4.8, 3.9, 4.1, 4.4, 4.6])
        assert d.measured
        assert 3.9 < d.mean < 4.4

    def test_zero_variance_does_not_divide_by_zero(self):
        d = distribution_for(SourceName.YELP, [4.0] * 12)
        assert d.std == 0.0
        assert d.safe_std >= C.MIN_STD
        # And scoring a place against it must not explode.
        assert 0 <= score_place([yelp(4.0, 100)], {SourceName.YELP: d}, NOW).composite_score <= 100


class TestGeneralProperties:
    def test_score_is_bounded(self):
        extreme = score_place(
            [google(5.0, 10**6), yelp(5.0, 10**5), reddit(1.0, 10**6, 500), blog()], DISTS, NOW
        )
        assert 0.0 <= extreme.composite_score <= 100.0

    def test_no_signals_scores_zero(self):
        assert score_place([], DISTS, NOW).composite_score == 0.0

    def test_deterministic(self):
        signals = [google(4.6, 800), yelp(4.4, 300), reddit(0.3, 600, 3)]
        a = score_place(signals, DISTS, NOW)
        b = score_place(signals, DISTS, NOW)
        assert a.composite_score == b.composite_score

    def test_breakdown_is_json_serializable(self):
        import json

        result = score_place([google(4.6, 800), reddit(0.3, 600, 3), blog()], DISTS, NOW)
        payload = json.dumps(result.as_dict())
        assert "model_version" in payload
        assert len(result.as_dict()["signals"]) == 3
