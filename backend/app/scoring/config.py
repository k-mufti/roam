"""Scoring parameters, isolated so the model is tunable without editing logic.

Every constant here is a judgement call. They are gathered in one file with the
reasoning attached, because the honest answer to "why 0.85?" is "it is a prior,
here is what it encodes" — and that is much easier to defend than the same
number buried in an expression.
"""

from __future__ import annotations

from app.models.enums import SourceName

#: Prior trust in each source, before any evidence about a specific place.
#:
#: These are not "which site is better". They encode how much a *single*
#: signal from that source should move a ranking:
#:
#: * google_places (1.00) — by far the largest review samples and the only
#:   source with reliable coordinates, hours and categories.
#: * yelp (0.85) — smaller samples in Madrid than in the US, but its rating
#:   distribution is wider, so a Yelp rating discriminates *more* per review.
#:   Discounted for coverage, not for quality.
#: * reddit (0.80) — no star ratings, but opinions come from people who live
#:   there and are voted on by others who do. High signal, high variance.
#: * blog (0.45) — editorial inclusion is real evidence, but it is one
#:   editor's opinion with no volume behind it and no way to disagree.
SOURCE_CREDIBILITY: dict[SourceName, float] = {
    SourceName.GOOGLE_PLACES: 1.00,
    SourceName.YELP: 0.85,
    SourceName.REDDIT: 0.80,
    SourceName.BLOG: 0.45,
}

#: Fallback rating distribution per source, used when the database holds too
#: few rated places to measure one (see `MIN_SAMPLES_FOR_DISTRIBUTION`).
#: Google's Madrid mean really is ~4.5 with a tight spread — that compression
#: is the whole reason ratings must be centred per source before comparison.
FALLBACK_DISTRIBUTION: dict[SourceName, tuple[float, float]] = {
    SourceName.GOOGLE_PLACES: (4.45, 0.25),
    SourceName.YELP: (4.10, 0.45),
    SourceName.REDDIT: (0.0, 0.40),
    SourceName.BLOG: (0.0, 1.0),
}

#: Below this many rated places for a source, use the fallback distribution
#: instead of the measured one — a std computed from 4 ratings is noise.
MIN_SAMPLES_FOR_DISTRIBUTION = 8
#: Never divide by a std smaller than this.
MIN_STD = 0.08

#: Bayesian shrinkage strength, in "virtual reviews at the source mean".
#: A place with 50 reviews is weighted equally between its own rating and the
#: source's mean; with 900 reviews its own rating dominates. This is the
#: IMDb-style weighted rating, and it is what makes "4.5 with 900 reviews"
#: outrank "4.5 with 3 reviews" rather than tying.
SHRINKAGE_PRIOR_REVIEWS = 50.0

#: Review volume at which a signal's weight reaches half its maximum.
VOLUME_HALF_SATURATION = 120.0

#: Recency half-life. A signal observed this long ago counts half as much.
#: 18 months: long enough that a restaurant's reputation is still informative,
#: short enough that a 2019 thread does not outvote a recent one.
RECENCY_HALF_LIFE_DAYS = 540.0
#: Floor on recency decay — old evidence is weaker, never worthless.
MIN_RECENCY_WEIGHT = 0.15

#: Slope of the logistic mapping a per-source z-score into 0-1 quality.
#: 0.8 puts a +2σ place at ~0.83 and a -2σ place at ~0.17: a usable spread
#: without saturating at the tails.
Z_TO_QUALITY_SLOPE = 0.8

# --- Reddit-specific --------------------------------------------------------

#: Evidence (log upvotes + mentions) at which Reddit sentiment is trusted at
#: half strength. Below it, sentiment is shrunk toward neutral: a single +2
#: comment should not read as a strong opinion.
REDDIT_EVIDENCE_HALF_SATURATION = 6.0
#: A thread's own score contributes to weight — an opinion in a 2,800-upvote
#: thread was seen and endorsed by more people than one in a 30-upvote thread.
REDDIT_THREAD_SCORE_HALF_SATURATION = 400.0

# --- Blog-specific ----------------------------------------------------------

#: Quality assigned to editorial inclusion. Above neutral (being listed is a
#: recommendation) but well below a rave, since the source expresses no degree.
BLOG_INCLUSION_QUALITY = 0.66

# --- Corroboration ----------------------------------------------------------

#: Maximum additive bonus, in 0-1 score units, for multi-source agreement.
#: Additive rather than multiplicative so it cannot rescue a badly-rated place,
#: and bounded at 0.12 so corroboration shifts rankings without dominating them.
CORROBORATION_MAX_BONUS = 0.12
#: Disagreement (weighted std of per-source quality) at which the agreement
#: factor falls to zero. Sources differing by more than this get no bonus, even
#: if there are four of them.
CORROBORATION_MAX_DISAGREEMENT = 0.30

# --- Evidence confidence ----------------------------------------------------

#: Total signal weight at which the base quality is trusted at half strength.
#:
#: This exists because a weighted mean is scale-invariant: with a single signal,
#: the weight cancels out entirely, so one low-credibility blog listing produced
#: exactly the same base quality as one 87,000-review Google rating. In practice
#: that ranked every blog-only place at 66/100, above well-reviewed places with
#: three corroborating sources.
#:
#: So the base is shrunk toward a neutral 0.5 in proportion to how much total
#: evidence there is. This is distinct from the corroboration bonus and not a
#: duplicate of it: evidence confidence asks "how much do we know?", agreement
#: asks "do the sources concur?". Four sources that disagree score high on the
#: first and zero on the second.
EVIDENCE_HALF_WEIGHT = 0.35
#: The prior a place regresses to when evidence is thin. 0.5 = "unremarkable".
NEUTRAL_QUALITY = 0.5

#: Final scores are reported on 0-100.
SCORE_SCALE = 100.0
