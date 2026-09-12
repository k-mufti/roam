"""Source-credibility weighted composite scoring.

This is the project's core differentiator, so it is written as pure functions
over plain dataclasses with no database or ORM anywhere — it can be exercised,
tested and demoed entirely in isolation (`trip explain "<place>"`).

## Why not average the star ratings

Three reasons, each fixed by a distinct mechanism below.

1. **Ratings are not comparable across sources.** Google's Madrid mean is about
   4.45 with a standard deviation of ~0.25; Yelp's is about 4.10 with a much
   wider spread. So a 4.3 is *below* average on Google and *above* average on
   Yelp. Averaging them treats those as the same statement.
   → Fix: **per-source z-scoring**. Each rating is centred and scaled by that
   source's own measured distribution before anything is combined.

2. **Confidence varies wildly.** 4.5 from 3 reviewers and 4.5 from 900 are not
   the same claim.
   → Fix: **Bayesian shrinkage** of the rating toward the source mean by review
   volume, *and* a separate volume term in the signal's weight. Shrinkage moves
   the estimate; volume moves how much the estimate counts.

3. **Agreement is information that averaging discards.** A place liked by
   Google, Yelp and Reddit independently is a safer recommendation than one
   liked only by Google, even at identical stars.
   → Fix: an explicit **corroboration bonus** scaled by how much the sources
   actually agree, not merely by how many there are.

## Shape of the computation

    per signal:  quality  q_s  in 0..1   (what this source says)
                 weight   w_s  in 0..1   (credibility x volume x recency)

    raw_base   = Σ(q_s · w_s) / Σ(w_s)
    evidence   = Σ(w_s)
    confidence = evidence / (evidence + EVIDENCE_HALF_WEIGHT)
    base       = 0.5 + (raw_base - 0.5) · confidence
    agreement  = 1 - weighted_std(q_s) / MAX_DISAGREEMENT
    bonus      = MAX_BONUS · (1 - 1/n_sources) · agreement
    composite  = 100 · min(1, base + bonus)

The `confidence` step matters more than it looks. A weighted mean is
scale-invariant, so with one signal the weight cancels and a lone blog listing
scored identically to a lone 87,000-review Google rating. Shrinking toward a
neutral prior by *total* evidence weight is what makes thin evidence read as
"unremarkable" rather than as whatever the single source happened to say.

The bonus is additive and capped, so corroboration reorders comparable places
without letting a well-corroborated mediocre place outrank an excellent one.

## Sources without ratings

Reddit and the blog scraper have no stars, and inventing some would launder a
guess into the score. They get explicit, documented derivations instead:

* **Reddit** — quality comes from upvote-weighted sentiment, shrunk toward
  neutral by how much evidence there is (`log1p(upvotes) + mention_count`), so
  one +2 comment cannot read as a strong opinion. Upvotes and thread score
  drive its *weight*, per the spec's requirement to weight by thread upvotes and
  sentiment rather than raw mention count.
* **Blog** — inclusion in a curated list is treated as a fixed, mildly positive
  quality with a low weight. The source expresses no degree, so neither do we.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.models.enums import SourceName
from app.scoring import config as C

# --- inputs -----------------------------------------------------------------


@dataclass(slots=True)
class SignalInput:
    """One source's signal, decoupled from the ORM."""

    source: SourceName
    rating: float | None = None
    review_count: int | None = None
    observed_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceDistribution:
    """Rating distribution for one source, measured or fallback."""

    mean: float
    std: float
    sample_size: int
    measured: bool

    @property
    def safe_std(self) -> float:
        return max(self.std, C.MIN_STD)


def distribution_for(source: SourceName, ratings: list[float]) -> SourceDistribution:
    """Measure a source's rating distribution, falling back when data is thin."""
    usable = [r for r in ratings if r is not None]
    if len(usable) < C.MIN_SAMPLES_FOR_DISTRIBUTION:
        mean, std = C.FALLBACK_DISTRIBUTION.get(source, (4.2, 0.4))
        return SourceDistribution(mean, std, len(usable), measured=False)
    mean = sum(usable) / len(usable)
    variance = sum((r - mean) ** 2 for r in usable) / max(1, len(usable) - 1)
    return SourceDistribution(mean, math.sqrt(variance), len(usable), measured=True)


# --- outputs ----------------------------------------------------------------


@dataclass(slots=True)
class SignalScore:
    """Fully explained contribution of one signal."""

    source: SourceName
    quality: float
    weight: float
    credibility: float
    volume_weight: float
    recency_weight: float
    raw_rating: float | None
    shrunk_rating: float | None
    z_score: float | None
    review_count: int | None
    age_days: float | None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "quality": round(self.quality, 4),
            "weight": round(self.weight, 4),
            "weight_components": {
                "credibility": round(self.credibility, 4),
                "volume": round(self.volume_weight, 4),
                "recency": round(self.recency_weight, 4),
            },
            "raw_rating": self.raw_rating,
            "shrunk_rating": (
                round(self.shrunk_rating, 3) if self.shrunk_rating is not None else None
            ),
            "z_score": round(self.z_score, 3) if self.z_score is not None else None,
            "review_count": self.review_count,
            "age_days": round(self.age_days, 1) if self.age_days is not None else None,
            "notes": self.notes,
        }


@dataclass(slots=True)
class ScoreResult:
    composite_score: float
    base: float
    corroboration_bonus: float
    agreement: float
    source_count: int
    signals: list[SignalScore]
    #: Weighted mean quality before evidence shrinkage.
    raw_base: float = 0.0
    #: Σ of signal weights.
    evidence: float = 0.0
    #: evidence / (evidence + EVIDENCE_HALF_WEIGHT)
    evidence_confidence: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "composite_score": round(self.composite_score, 2),
            "raw_base": round(self.raw_base, 4),
            "evidence": round(self.evidence, 4),
            "evidence_confidence": round(self.evidence_confidence, 4),
            "base": round(self.base, 4),
            "corroboration_bonus": round(self.corroboration_bonus, 4),
            "agreement": round(self.agreement, 4),
            "source_count": self.source_count,
            "signals": [s.as_dict() for s in self.signals],
            "model_version": MODEL_VERSION,
        }


#: Bump when the model changes, so persisted breakdowns are interpretable.
MODEL_VERSION = "1.0"


# --- primitives -------------------------------------------------------------


def sigmoid(x: float) -> float:
    if x < -60:
        return 0.0
    if x > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def shrink_rating(rating: float, review_count: int | None, prior_mean: float) -> float:
    """Pull a rating toward the source mean in proportion to how thin its
    sample is. IMDb-style weighted rating."""
    n = float(max(0, review_count or 0))
    m = C.SHRINKAGE_PRIOR_REVIEWS
    return (n * rating + m * prior_mean) / (n + m)


def volume_weight(review_count: int | None) -> float:
    """Saturating confidence from review volume, in 0-1.

    A source with no volume information (Reddit's mention count is handled
    separately; the blog has none) gets a deliberately low 0.35 rather than 0:
    the signal still exists, it just carries no sample behind it.
    """
    if review_count is None:
        return 0.35
    n = float(max(0, review_count))
    return n / (n + C.VOLUME_HALF_SATURATION)


def recency_weight(observed_at: datetime | None, now: datetime) -> tuple[float, float | None]:
    """Exponential decay by age. Returns (weight, age_days)."""
    if observed_at is None:
        # Unknown age: neither penalise nor reward.
        return 0.7, None
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    age_days = max(0.0, (now - observed_at).total_seconds() / 86400.0)
    decay = 0.5 ** (age_days / C.RECENCY_HALF_LIFE_DAYS)
    return max(C.MIN_RECENCY_WEIGHT, decay), age_days


# --- per-source quality -----------------------------------------------------


def _score_rated_signal(
    signal: SignalInput, dist: SourceDistribution, now: datetime
) -> SignalScore:
    """Google / Yelp: a real star rating on a known distribution."""
    assert signal.rating is not None
    shrunk = shrink_rating(signal.rating, signal.review_count, dist.mean)
    z = (shrunk - dist.mean) / dist.safe_std
    quality = sigmoid(z * C.Z_TO_QUALITY_SLOPE)

    credibility = C.SOURCE_CREDIBILITY.get(signal.source, 0.5)
    vol = volume_weight(signal.review_count)
    rec, age = recency_weight(signal.observed_at, now)

    notes = [
        f"rating {signal.rating} shrunk to {shrunk:.2f} toward the "
        f"{signal.source.value} mean of {dist.mean:.2f} "
        f"({signal.review_count or 0} reviews vs {C.SHRINKAGE_PRIOR_REVIEWS:.0f} prior)",
        f"z-score {z:+.2f} against this source's own spread (sd {dist.safe_std:.2f}"
        f"{', measured' if dist.measured else ', fallback prior'})",
    ]
    return SignalScore(
        source=signal.source,
        quality=quality,
        weight=credibility * vol * rec,
        credibility=credibility,
        volume_weight=vol,
        recency_weight=rec,
        raw_rating=signal.rating,
        shrunk_rating=shrunk,
        z_score=z,
        review_count=signal.review_count,
        age_days=age,
        notes=notes,
    )


def _score_reddit_signal(signal: SignalInput, now: datetime) -> SignalScore:
    """Reddit: sentiment as quality, upvotes and thread score as weight."""
    extra = signal.extra or {}
    sentiment = float(extra.get("sentiment") or 0.0)
    upvotes = float(extra.get("upvotes") or 0)
    mentions = float(extra.get("mention_count") or signal.review_count or 0)
    thread_score = float(extra.get("thread_score") or 0)

    # Evidence: log-damped upvotes plus mention count. Shrinks sentiment toward
    # neutral (0.5 quality) when there is little to go on.
    evidence = math.log1p(max(0.0, upvotes)) + mentions
    confidence = evidence / (evidence + C.REDDIT_EVIDENCE_HALF_SATURATION)
    quality = 0.5 + 0.5 * sentiment * confidence

    credibility = C.SOURCE_CREDIBILITY[SourceName.REDDIT]
    # Weight blends how endorsed the opinion was with how visible the thread was.
    upvote_term = evidence / (evidence + C.REDDIT_EVIDENCE_HALF_SATURATION)
    thread_term = thread_score / (thread_score + C.REDDIT_THREAD_SCORE_HALF_SATURATION)
    vol = 0.65 * upvote_term + 0.35 * thread_term
    rec, age = recency_weight(signal.observed_at, now)

    notes = [
        f"no star rating on Reddit; quality derived from upvote-weighted "
        f"sentiment {sentiment:+.3f}",
        f"{int(mentions)} mention(s), {int(upvotes)} upvote(s) -> evidence "
        f"{evidence:.2f}, sentiment trusted at {confidence:.0%}",
        f"best thread score {int(thread_score)} -> visibility term {thread_term:.2f}",
    ]
    return SignalScore(
        source=signal.source,
        quality=max(0.0, min(1.0, quality)),
        weight=credibility * vol * rec,
        credibility=credibility,
        volume_weight=vol,
        recency_weight=rec,
        raw_rating=None,
        shrunk_rating=None,
        z_score=None,
        review_count=int(mentions) or None,
        age_days=age,
        notes=notes,
    )


def _score_blog_signal(signal: SignalInput, now: datetime) -> SignalScore:
    """Blog/editorial: inclusion is the signal, with no degree attached."""
    credibility = C.SOURCE_CREDIBILITY[SourceName.BLOG]
    vol = volume_weight(None)
    rec, age = recency_weight(signal.observed_at, now)
    section = (signal.extra or {}).get("section")
    return SignalScore(
        source=signal.source,
        quality=C.BLOG_INCLUSION_QUALITY,
        weight=credibility * vol * rec,
        credibility=credibility,
        volume_weight=vol,
        recency_weight=rec,
        raw_rating=None,
        shrunk_rating=None,
        z_score=None,
        review_count=None,
        age_days=age,
        notes=[
            "editorially listed"
            + (f" under '{section}'" if section else "")
            + f"; no rating expressed, so a flat {C.BLOG_INCLUSION_QUALITY:.2f} quality "
            f"at low weight"
        ],
    )


def score_signal(
    signal: SignalInput, distributions: dict[SourceName, SourceDistribution], now: datetime
) -> SignalScore:
    if signal.source is SourceName.REDDIT:
        return _score_reddit_signal(signal, now)
    if signal.source is SourceName.BLOG:
        return _score_blog_signal(signal, now)
    if signal.rating is None:
        # A rated source that happens to be missing its rating: treat as
        # inclusion-only rather than dropping the signal entirely.
        return _score_blog_signal(signal, now)
    dist = distributions.get(signal.source) or distribution_for(signal.source, [])
    return _score_rated_signal(signal, dist, now)


# --- corroboration ----------------------------------------------------------


def corroboration(signal_scores: list[SignalScore]) -> tuple[float, float]:
    """Return (agreement, bonus).

    `agreement` is 1 when independent sources report the same quality and falls
    to 0 as their weighted spread reaches CORROBORATION_MAX_DISAGREEMENT. The
    bonus then scales with both agreement and how many sources there are, via
    (1 - 1/n): two sources capture half the available bonus, four capture 75%.
    Diminishing returns are intentional — the second independent source is the
    informative one.
    """
    distinct = {s.source for s in signal_scores}
    n = len(distinct)
    if n < 2:
        return 1.0, 0.0

    total_weight = sum(s.weight for s in signal_scores)
    if total_weight <= 0:
        return 1.0, 0.0
    mean = sum(s.quality * s.weight for s in signal_scores) / total_weight
    variance = sum(s.weight * (s.quality - mean) ** 2 for s in signal_scores) / total_weight
    spread = math.sqrt(variance)

    agreement = max(0.0, 1.0 - spread / C.CORROBORATION_MAX_DISAGREEMENT)
    bonus = C.CORROBORATION_MAX_BONUS * (1.0 - 1.0 / n) * agreement
    return agreement, bonus


# --- top level --------------------------------------------------------------


def score_place(
    signals: list[SignalInput],
    distributions: dict[SourceName, SourceDistribution] | None = None,
    now: datetime | None = None,
) -> ScoreResult:
    """Compute a place's composite score with a full explanation."""
    now = now or datetime.now(UTC)
    distributions = distributions or {}

    scored = [score_signal(s, distributions, now) for s in signals]
    if not scored:
        return ScoreResult(0.0, 0.0, 0.0, 1.0, 0, [])

    evidence = sum(s.weight for s in scored)
    if evidence <= 0:
        raw_base = sum(s.quality for s in scored) / len(scored)
    else:
        raw_base = sum(s.quality * s.weight for s in scored) / evidence

    # Shrink toward the neutral prior by how much total evidence exists.
    confidence = evidence / (evidence + C.EVIDENCE_HALF_WEIGHT)
    base = C.NEUTRAL_QUALITY + (raw_base - C.NEUTRAL_QUALITY) * confidence

    agreement, bonus = corroboration(scored)
    composite = min(1.0, max(0.0, base + bonus)) * C.SCORE_SCALE

    return ScoreResult(
        composite_score=composite,
        base=base,
        corroboration_bonus=bonus,
        agreement=agreement,
        source_count=len({s.source for s in scored}),
        signals=sorted(scored, key=lambda s: -s.weight),
        raw_base=raw_base,
        evidence=evidence,
        evidence_confidence=confidence,
    )
