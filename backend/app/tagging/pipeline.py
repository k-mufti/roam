"""Tagging pipeline: text evidence + structured facts -> normalized tags.

Three independent tag producers, combined:

1. `lexicon`    — weighted, negation-aware phrase matching over review text.
2. `structural` — price tier, opening hours, category, cross-source consensus.
3. `keywords`   — corpus-relative TF-IDF, emitted as `topic:` tags.

Tags are recomputed from scratch on every run rather than accumulated, so a
lexicon fix actually removes wrong tags instead of layering new ones on top.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ingestion.mentions import sentiment as score_sentiment
from app.ingestion.normalize import strip_accents
from app.models import Place, TextEvidence
from app.tagging import structural
from app.tagging.keywords import extract_keywords
from app.tagging.lexicon import TAG_RULES, TagEvidence, matches_any, phrase_hits

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Snippet:
    text: str
    weight: float
    sentiment: float


@dataclass(slots=True)
class TagResult:
    tags: list[str] = field(default_factory=list)
    evidence: list[TagEvidence] = field(default_factory=list)


def tags_from_text(snippets: list[Snippet]) -> TagResult:
    """Apply every lexicon rule to a place's snippets.

    A rule fires when the weighted share of snippets matching it clears the
    rule's threshold. Weighting by snippet weight (Reddit upvotes) rather than
    counting snippets is what makes a single highly-endorsed comment able to
    carry a tag, while a stray remark in an unvoted comment cannot.
    """
    result = TagResult()
    if not snippets:
        return result

    total_weight = sum(max(0.0, s.weight) for s in snippets) or float(len(snippets))

    for rule in TAG_RULES:
        matched_weight = 0.0
        matched_phrases: set[str] = set()
        samples: list[str] = []
        sentiments: list[float] = []
        vetoed = False

        for snippet in snippets:
            if matches_any(snippet.text, rule.exclude):
                # An explicit veto anywhere in the evidence kills the tag. That
                # is intentional: "no laptops" is a statement about policy, and
                # one such statement outranks any number of vague mentions.
                vetoed = True
                break
            hits = [p for p in rule.include if phrase_hits(snippet.text, p)]
            if not hits:
                continue
            matched_weight += max(0.0, snippet.weight)
            matched_phrases.update(hits)
            sentiments.append(snippet.sentiment)
            if len(samples) < 3:
                samples.append(snippet.text[:220])

        if vetoed or not matched_phrases:
            continue

        share = matched_weight / total_weight if total_weight else 0.0
        if share < rule.min_evidence_share:
            continue
        if rule.min_sentiment is not None and sentiments:
            if (sum(sentiments) / len(sentiments)) < rule.min_sentiment:
                continue

        result.tags.append(rule.tag)
        result.evidence.append(
            TagEvidence(
                tag=rule.tag,
                share=round(share, 4),
                matched_phrases=sorted(matched_phrases),
                sample_snippets=samples,
            )
        )
    return result


def _snippets_for(place: Place, evidence: list[TextEvidence]) -> list[Snippet]:
    out = []
    for row in evidence:
        text = (row.text or "").strip()
        if not text:
            continue
        # Sentiment is stored for Reddit-sourced evidence but not for Google
        # editorial summaries or scraped descriptions; compute it on demand so
        # rules with a sentiment floor work uniformly across sources.
        value = row.sentiment if row.sentiment is not None else score_sentiment(text)
        out.append(Snippet(text=text, weight=max(0.1, row.weight or 1.0), sentiment=value))
    return out


def retag_city(session: Session, city: str) -> int:
    """Recompute tags for every canonical place in a city."""
    places = list(
        session.execute(
            select(Place)
            .options(selectinload(Place.signals))
            .where(Place.city == city, Place.duplicate_of.is_(None))
        ).scalars()
    )
    if not places:
        return 0

    place_ids = [p.id for p in places]
    evidence_by_place: dict[object, list[TextEvidence]] = defaultdict(list)
    for row in session.execute(
        select(TextEvidence).where(TextEvidence.place_id.in_(place_ids))
    ).scalars():
        evidence_by_place[row.place_id].append(row)

    # TF-IDF needs the whole corpus at once.
    documents = {
        str(p.id): " ".join(r.text for r in evidence_by_place.get(p.id, []))
        for p in places
    }
    # Veto each place's own name tokens as keywords — they have maximal TF-IDF
    # and zero information.
    name_tokens = {
        str(p.id): set(p.name_normalized.split())
        | set(strip_accents(p.name).lower().replace("-", " ").split())
        for p in places
    }
    keywords = extract_keywords(documents, exclude_terms=name_tokens)
    log.info("TF-IDF produced keywords for %d/%d places", len(keywords), len(places))

    for place in places:
        snippets = _snippets_for(place, evidence_by_place.get(place.id, []))
        text_result = tags_from_text(snippets)

        signals_by_source = {
            s.source: {"review_count": s.review_count, **(s.extra or {})}
            for s in place.signals
        }

        tags: set[str] = set(text_result.tags)
        tags |= structural.price_tags(place.price_tier)
        tags |= structural.category_tags(place.category)
        tags |= structural.hours_tags(place.hours)
        tags |= structural.consensus_tags(place.composite_score, signals_by_source)
        tags |= set(keywords.get(str(place.id), []))

        # Mutual exclusions. Reviewers genuinely disagree, so the lexicon can
        # emit both halves of a contradictory pair; leaving both in makes the
        # filters useless ("quiet AND lively" matches nothing a user means).
        shares = {e.tag: e.share for e in text_result.evidence}

        # Cost: resolve from the structured price tier, which beats prose.
        if "budget" in tags and "pricey" in tags:
            tags.discard("pricey" if (place.price_tier or 0) <= 2 else "budget")
        # Atmosphere: no structured ground truth, so the stronger weighted
        # evidence share wins; a tie drops both rather than picking arbitrarily.
        if "quiet" in tags and "lively" in tags:
            quiet, lively = shares.get("quiet", 0.0), shares.get("lively", 0.0)
            if quiet > lively:
                tags.discard("lively")
            elif lively > quiet:
                tags.discard("quiet")
            else:
                tags -= {"quiet", "lively"}
        # A place cannot be both a local secret and a tourist trap.
        if "hidden-gem" in tags and {"tourist-trap", "touristy"} & tags:
            tags.discard("hidden-gem")

        place.tags = sorted(tags)

    session.flush()
    log.info("tagged %d places in %s", len(places), city)
    return len(places)
