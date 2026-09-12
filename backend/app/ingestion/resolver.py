"""Entity resolution: turn `RawPlace` records from N sources into one row each.

The problem: "Café Rivas" (Yelp), "Cafe Rivas Palermo" (Google) and a bare
"rivas" mention (Reddit) are one physical cafe. Naive inserts would produce
three rows, three mediocre scores, and three map pins on top of each other.

Strategy — blocking, then scoring, then a three-way decision:

1. **Identity short-circuit.** If `(source, source_place_id)` already exists we
   are re-ingesting; update in place. This makes the whole pipeline idempotent.

2. **Blocking.** Pull a small candidate set instead of comparing against every
   row: canonical places in the same city that are either within
   `BLOCK_RADIUS_M` (PostGIS `ST_DWithin` on the geography column, GiST index)
   or trigram-similar by name (pg_trgm GIN index). Blocking is what keeps this
   O(candidates) rather than O(n²).

3. **Scoring.** Blend fuzzy name similarity with geographic proximity. Name
   similarity uses rapidfuzz's `token_set_ratio`, which is the right metric
   here because sources differ by *extra* tokens ("Palermo", "Restaurante")
   rather than by typos — `token_set_ratio` ignores extra tokens that both
   strings don't share, where plain Levenshtein would penalize them heavily.

4. **Decision.** A *distance-tiered rule table* (`MERGE_RULES`), not one
   blended threshold. An earlier version scored name similarity and proximity
   into a single number and merged above a cutoff; it failed on real data,
   flagging four pairs that were unmistakably the same place 14-15m apart
   ("Museo del Prado" / "Museo Nacional del Prado", "Retiro Park" / "Parque de
   El Retiro"), because cross-language names only reach ~0.67 fuzzy similarity
   and a linear blend cannot express "at 15m the names barely need to agree".

   The tiers encode what distance actually means:

   * **Same footprint (<=40m)** — almost certainly one building, so a
     *distinctive shared token* is enough ("retiro", "prado", "botin"). This
     is where cross-language and abbreviated names get resolved.
   * **Same block (<=120m)** — names must agree strongly on their own.
   * **Same street (<=250m)** — near-identical names only.

   The distinctive-token requirement in tier 1 is what stops distance alone
   from over-merging: stalls inside a food hall are metres apart, and "Casa
   Lucio" / "Casa Botín" are 40m apart, but "casa" is a generic token and
   contributes no anchor, so they are not merged.

5. **Never guess in between.** If no rule fires but confidence still clears
   `REVIEW_CONFIDENCE`, we create the place *and* write a `MergeReview` row for
   a human. Guessing in the ambiguous band is how dedup pipelines quietly
   corrupt their own data.

Coordinate-less mentions (Reddit, blog) take a stricter path: name-only match
against places an authoritative source already geocoded, at a much higher
threshold, and are dropped if nothing matches.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from rapidfuzz import fuzz
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.ingestion.base import RawPlace
from app.ingestion.normalize import (
    content_hash,
    distinctive_tokens,
    haversine_m,
    normalize_name,
)
from app.models import MergeDecision, MergeReview, Place, PlaceCategory, SourceSignal, TextEvidence

log = logging.getLogger(__name__)

# --- Tunables ---------------------------------------------------------------
# These are module constants rather than settings because changing them changes
# data quality, so they belong in code review and in the test suite.

#: Candidate blocking radius. Generous on purpose — cheap to over-fetch here,
#: expensive to miss a true duplicate.
BLOCK_RADIUS_M = 400.0
#: Beyond this, two records are not the same place, whatever the names say.
MAX_MERGE_DISTANCE_M = 250.0
#: Minimum fuzzy name similarity for a pair to be scored at all.
MIN_NAME_SIMILARITY = 0.55


@dataclass(frozen=True, slots=True)
class MergeRule:
    """One tier of the merge decision table."""

    max_distance_m: float
    min_name_similarity: float
    #: Require a shared distinctive (non-generic, >=4 char) name token.
    requires_anchor: bool
    label: str


#: Evaluated in order; the first rule whose conditions hold authorizes a merge.
MERGE_RULES: tuple[MergeRule, ...] = (
    MergeRule(40.0, 0.62, True, "same footprint + shared distinctive token"),
    MergeRule(120.0, 0.80, False, "same block + strong name agreement"),
    MergeRule(250.0, 0.92, False, "same street + near-identical name"),
)

#: Confidence at/above which an unmerged pair is still worth a human's time.
REVIEW_CONFIDENCE = 0.62
#: ...but confidence alone is not enough to enter the review queue. Proximity
#: contributes up to 0.45, so in a dense city centre *any* two records 15m apart
#: clear REVIEW_CONFIDENCE regardless of their names — which would fill the
#: queue with pairs like "Casa Dani" (a stall) and "Mercado de la Paz" (the
#: market containing it). A pair must also be nameally confusable to be worth
#: a human's attention.
REVIEW_MIN_NAME_SIMILARITY = 0.60

#: Half-distance of the proximity curve: proximity(90m) == 0.5. The curve is
#: 1/(1+(d/d_half)^2) rather than linear because co-location is sharply more
#: informative at short range — 15m and 40m are both "same building", while
#: 150m and 250m are both "probably not".
PROXIMITY_HALF_DISTANCE_M = 90.0

#: Coordinate-less mentions need near-certainty on the name alone...
NAME_ONLY_SIMILARITY = 0.90
#: ...*or* an unambiguous distinctive-token containment. Forum prose abbreviates
#: constantly — "the Prado", "Retiro", "Botin", "Thyssen" — and those score only
#: 0.67-0.82 against the full official names, so a similarity-only rule discards
#: some of the strongest signals in the corpus. Containment is accepted when the
#: mention's distinctive tokens are a subset of exactly one candidate's, which is
#: what keeps it safe: "Guernica" and "Malasana" contain nothing, and an
#: abbreviation matching two places is ambiguous and dropped rather than guessed.
NAME_ONLY_ALLOW_TOKEN_CONTAINMENT = True

#: Extra-field combination rules for sources that emit one signal per *mention*
#: but must persist one signal per *place* (Reddit: "Prado" and "Museo del
#: Prado" are two mentions of one place). Without this the place would collect
#: two Reddit signals and be double-counted in scoring.
ADDITIVE_EXTRA_KEYS = frozenset({"mention_count", "upvotes", "thread_count"})
MAX_EXTRA_KEYS = frozenset({"thread_score"})
#: Averaged, weighted by `upvotes`, so a 900-upvote opinion dominates a 5-upvote one.
UPVOTE_WEIGHTED_EXTRA_KEYS = frozenset({"sentiment"})


@dataclass(slots=True)
class ResolutionOutcome:
    place: Place | None
    decision: MergeDecision
    confidence: float
    reason: str


@dataclass(slots=True)
class ResolutionStats:
    created: int = 0
    merged: int = 0
    flagged: int = 0
    updated: int = 0
    dropped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "created": self.created,
            "merged": self.merged,
            "flagged": self.flagged,
            "updated": self.updated,
            "dropped": self.dropped,
        }


def name_similarity(left: str, right: str) -> float:
    """Fuzzy name similarity in 0-1.

    A blend of two rapidfuzz ratios, because each fails differently:

    * `token_set_ratio` handles extra/missing tokens ("Cafe Rivas" vs
      "Cafe Rivas Palermo") but is too forgiving of short strings that share a
      common word ("Casa Lucio" vs "Casa Botin" both contain "casa").
    * `partial_ratio` catches substring containment.
    * `ratio` is the strict baseline that pulls the blend back down when the
      strings genuinely differ.

    Taking the max of the first two and averaging with the strict ratio keeps
    the generous behaviour where it's warranted without letting "Casa X" match
    "Casa Y".
    """
    if not left or not right:
        return 0.0
    generous = max(fuzz.token_set_ratio(left, right), fuzz.partial_ratio(left, right)) / 100.0
    strict = fuzz.ratio(left, right) / 100.0
    return (generous + strict) / 2.0


def proximity_score(distance_m: float) -> float:
    """Non-linear closeness in 0-1. See PROXIMITY_HALF_DISTANCE_M."""
    if distance_m <= 0:
        return 1.0
    ratio = distance_m / PROXIMITY_HALF_DISTANCE_M
    return 1.0 / (1.0 + ratio * ratio)


def merge_confidence(name_sim: float, distance_m: float, anchored: bool = False) -> float:
    """A single auditable 0-1 number for the pair.

    This no longer *decides* the merge — `MERGE_RULES` does — but it is stored
    on the place and in `merge_reviews`, and it orders the human review queue,
    so it still needs to be meaningful.
    """
    if distance_m > MAX_MERGE_DISTANCE_M:
        return 0.0
    score = 0.55 * name_sim + 0.45 * proximity_score(distance_m)
    if anchored:
        score += 0.05
    return min(1.0, score)


def matching_rule(name_sim: float, distance_m: float, anchored: bool) -> MergeRule | None:
    """First merge rule the pair satisfies, or None."""
    for rule in MERGE_RULES:
        if distance_m > rule.max_distance_m:
            continue
        if name_sim < rule.min_name_similarity:
            continue
        if rule.requires_anchor and not anchored:
            continue
        return rule
    return None


class EntityResolver:
    """Persists `RawPlace` records, deduplicating as it goes."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.stats = ResolutionStats()

    # --- public API ---------------------------------------------------------

    def ingest(self, raw: RawPlace) -> ResolutionOutcome:
        existing_signal = self._existing_signal(raw)
        if existing_signal is not None:
            place = existing_signal.place
            self._apply_signal(place, raw, existing_signal)
            self._enrich(place, raw)
            self.stats.updated += 1
            return ResolutionOutcome(place, MergeDecision.MERGED, 1.0, "known source identity")

        if raw.has_location:
            outcome = self._resolve_geocoded(raw)
        else:
            outcome = self._resolve_name_only(raw)

        if outcome.place is not None:
            self._apply_signal(outcome.place, raw, None)
            self._enrich(outcome.place, raw)
        return outcome

    def ingest_all(self, places: list[RawPlace]) -> ResolutionStats:
        for raw in places:
            try:
                self.ingest(raw)
            except Exception:
                log.exception("failed to ingest %s/%s", raw.source, raw.source_place_id)
                self.session.rollback()
        self.session.flush()
        return self.stats

    # --- resolution paths ---------------------------------------------------

    def _resolve_geocoded(self, raw: RawPlace) -> ResolutionOutcome:
        assert raw.lat is not None and raw.lng is not None
        key = normalize_name(raw.name)
        candidates = self._block_candidates(raw, key)

        anchors = distinctive_tokens(key)

        best: tuple[float, float, float, bool, MergeRule | None, Place] | None = None
        for cand in candidates:
            sim = name_similarity(key, cand.name_normalized)
            if sim < MIN_NAME_SIMILARITY:
                continue
            dist = haversine_m(raw.lat, raw.lng, cand.lat, cand.lng)
            anchored = bool(anchors & distinctive_tokens(cand.name_normalized))
            rule = matching_rule(sim, dist, anchored)
            conf = merge_confidence(sim, dist, anchored)
            # Prefer a candidate a rule authorizes; break ties on confidence.
            ranking = (rule is not None, conf)
            if best is None or ranking > (best[4] is not None, best[0]):
                best = (conf, sim, dist, anchored, rule, cand)

        if best is None:
            return ResolutionOutcome(
                self._create_place(raw), MergeDecision.CREATED, 0.0, "no similar candidate"
            )

        conf, sim, dist, anchored, rule, cand = best

        if rule is not None:
            cand.merge_confidence = conf
            self.stats.merged += 1
            return ResolutionOutcome(
                cand,
                MergeDecision.MERGED,
                conf,
                f"{rule.label} (name_sim={sim:.2f} distance={dist:.0f}m)",
            )

        place = self._create_place(raw)
        if conf >= REVIEW_CONFIDENCE and sim >= REVIEW_MIN_NAME_SIMILARITY:
            # Plausible duplicate, but no rule authorizes it. Keep both rows,
            # record the pair for a human, move on.
            self._flag(place, cand, sim, dist, conf, anchored)
            self.stats.flagged += 1
            return ResolutionOutcome(
                place,
                MergeDecision.FLAGGED,
                conf,
                f"ambiguous: name_sim={sim:.2f} distance={dist:.0f}m anchored={anchored}",
            )
        return ResolutionOutcome(
            place, MergeDecision.CREATED, conf, f"best candidate too weak (conf={conf:.2f})"
        )

    def _resolve_name_only(self, raw: RawPlace) -> ResolutionOutcome:
        """Attach a coordinate-less mention to an already-geocoded place.

        We never *create* from these. A Reddit comment saying "go to Botín" is
        a strong signal about a place, but it is not a place record: it has no
        coordinates, hours, or category, so it could not be mapped or routed.
        Unmatched mentions are counted and dropped.
        """
        key = normalize_name(raw.name)
        if not key:
            self.stats.dropped += 1
            return ResolutionOutcome(None, MergeDecision.CREATED, 0.0, "empty normalized name")

        anchors = distinctive_tokens(key)
        conditions = [
            Place.name_normalized == key,
            func.similarity(Place.name_normalized, key) > 0.4,
        ]
        if anchors:
            # Word-boundary match on any distinctive token, so "prado" finds
            # "museo nacional del prado" (trigram similarity alone would not:
            # the strings differ too much in length).
            pattern = r"\y(" + "|".join(re.escape(t) for t in sorted(anchors)) + r")\y"
            conditions.append(Place.name_normalized.op("~")(pattern))

        stmt = (
            select(Place)
            .where(Place.city == raw.city, Place.duplicate_of.is_(None), or_(*conditions))
            .limit(50)
        )
        candidates = list(self.session.execute(stmt).scalars())

        best: tuple[float, Place] | None = None
        contained: list[Place] = []
        for cand in candidates:
            sim = name_similarity(key, cand.name_normalized)
            if best is None or sim > best[0]:
                best = (sim, cand)
            if anchors and anchors <= distinctive_tokens(cand.name_normalized):
                contained.append(cand)

        if best is not None and best[0] >= NAME_ONLY_SIMILARITY:
            sim, cand = best
            self.stats.merged += 1
            return ResolutionOutcome(
                cand, MergeDecision.MERGED, sim, f"name-only match sim={sim:.2f}"
            )

        if NAME_ONLY_ALLOW_TOKEN_CONTAINMENT and len(contained) == 1:
            cand = contained[0]
            sim = name_similarity(key, cand.name_normalized)
            self.stats.merged += 1
            return ResolutionOutcome(
                cand,
                MergeDecision.MERGED,
                max(sim, 0.85),
                f"distinctive tokens {sorted(anchors)} uniquely contained in "
                f"{cand.name!r} (sim={sim:.2f})",
            )

        self.stats.dropped += 1
        got = f"{best[0]:.2f}" if best else "none"
        detail = (
            f" (ambiguous: {len(contained)} places contain {sorted(anchors)})"
            if len(contained) > 1
            else ""
        )
        return ResolutionOutcome(
            None,
            MergeDecision.CREATED,
            best[0] if best else 0.0,
            f"no geocoded place matched {raw.name!r} (best={got}){detail}",
        )

    # --- persistence helpers ------------------------------------------------

    def _existing_signal(self, raw: RawPlace) -> SourceSignal | None:
        return self.session.execute(
            select(SourceSignal).where(
                SourceSignal.source == raw.source,
                SourceSignal.source_place_id == raw.source_place_id,
            )
        ).scalar_one_or_none()

    def _block_candidates(self, raw: RawPlace, key: str) -> list[Place]:
        """Fetch a bounded candidate set using the spatial and trigram indexes."""
        point = func.ST_GeogFromText(f"SRID=4326;POINT({raw.lng} {raw.lat})")
        stmt = (
            select(Place)
            .where(
                Place.city == raw.city,
                Place.duplicate_of.is_(None),
                or_(
                    func.ST_DWithin(Place.geom, point, BLOCK_RADIUS_M),
                    func.similarity(Place.name_normalized, key) > 0.35,
                ),
            )
            .limit(50)
        )
        return list(self.session.execute(stmt).scalars())

    def _create_place(self, raw: RawPlace) -> Place:
        assert raw.lat is not None and raw.lng is not None
        place = Place(
            name=raw.name,
            name_normalized=normalize_name(raw.name),
            city=raw.city,
            country_code=raw.country_code,
            address=raw.address,
            neighborhood=raw.neighborhood,
            category=raw.category,
            price_tier=raw.price_tier,
            hours=raw.hours,
            tags=[],
        )
        place.set_location(raw.lat, raw.lng)
        self.session.add(place)
        self.session.flush()
        self.stats.created += 1
        return place

    def _apply_signal(self, place: Place, raw: RawPlace, existing: SourceSignal | None) -> None:
        """Write the source's signal, maintaining one signal per (place, source).

        That invariant is what `Place.source_signals` means in the spec — "one
        entry per source that mentions this place" — and scoring depends on it:
        two Reddit rows for the Prado would count Reddit twice, both inflating
        its weight and faking cross-source corroboration.
        """
        if existing is None:
            existing = self.session.execute(
                select(SourceSignal).where(
                    SourceSignal.place_id == place.id, SourceSignal.source == raw.source
                )
            ).scalars().first()

        if existing is not None and existing.source_place_id != raw.source_place_id:
            # Same source, same place, different mention identity: combine
            # instead of inserting a second row.
            self._combine_signal(existing, raw)
            self._attach_evidence(place, raw)
            self.session.flush()
            return

        signal = existing or SourceSignal(
            place_id=place.id, source=raw.source, source_place_id=raw.source_place_id
        )
        signal.place_id = place.id
        signal.rating = raw.rating
        signal.raw_rating = raw.raw_rating
        signal.review_count = raw.review_count
        signal.url = raw.url
        signal.extra = raw.extra or {}
        signal.observed_at = raw.observed_at
        signal.last_updated = datetime.now(UTC)
        if existing is None:
            self.session.add(signal)
        self._attach_evidence(place, raw)
        self.session.flush()

    @staticmethod
    def _combine_signal(signal: SourceSignal, raw: RawPlace) -> None:
        """Fold a second mention of the same place into an existing signal."""
        merged = dict(signal.extra or {})
        incoming = raw.extra or {}

        old_weight = float(merged.get("upvotes") or 0) + 1.0
        new_weight = float(incoming.get("upvotes") or 0) + 1.0

        for field in UPVOTE_WEIGHTED_EXTRA_KEYS:
            if field in incoming or field in merged:
                old = float(merged.get(field) or 0.0)
                new = float(incoming.get(field) or 0.0)
                merged[field] = round(
                    (old * old_weight + new * new_weight) / (old_weight + new_weight), 4
                )
        for field in ADDITIVE_EXTRA_KEYS:
            if field in incoming or field in merged:
                merged[field] = (merged.get(field) or 0) + (incoming.get(field) or 0)
        for field in MAX_EXTRA_KEYS:
            if field in incoming or field in merged:
                merged[field] = max(merged.get(field) or 0, incoming.get(field) or 0)
        for field, value in incoming.items():
            if field not in merged:
                merged[field] = value
        if isinstance(merged.get("subreddits"), list) and isinstance(
            incoming.get("subreddits"), list
        ):
            merged["subreddits"] = sorted(set(merged["subreddits"]) | set(incoming["subreddits"]))

        merged["merged_mentions"] = int(merged.get("merged_mentions") or 1) + 1
        signal.extra = merged
        signal.review_count = (signal.review_count or 0) + (raw.review_count or 0)
        if raw.observed_at and (
            signal.observed_at is None or raw.observed_at > signal.observed_at
        ):
            signal.observed_at = raw.observed_at
        signal.url = signal.url or raw.url
        signal.last_updated = datetime.now(UTC)

    def _attach_evidence(self, place: Place, raw: RawPlace) -> None:
        if not raw.evidence:
            return
        existing = {
            h
            for (h,) in self.session.execute(
                select(TextEvidence.content_hash).where(TextEvidence.place_id == place.id)
            )
        }
        for item in raw.evidence:
            text = item.text.strip()
            if not text:
                continue
            digest = content_hash(text)
            if digest in existing:
                continue
            existing.add(digest)
            self.session.add(
                TextEvidence(
                    place_id=place.id,
                    source=raw.source,
                    content_hash=digest,
                    text=text[:4000],
                    url=item.url,
                    weight=item.weight,
                )
            )

    def _enrich(self, place: Place, raw: RawPlace) -> None:
        """Fill gaps on a merged place without letting a weak source overwrite
        a strong one.

        Rule: only ever fill a NULL, except for `category`, where any concrete
        value beats OTHER. This means a Reddit mention can never downgrade a
        Google-sourced address, but a Google record can supply hours that Yelp
        lacked.
        """
        if place.address is None and raw.address:
            place.address = raw.address
        if place.neighborhood is None and raw.neighborhood:
            place.neighborhood = raw.neighborhood
        if place.country_code is None and raw.country_code:
            place.country_code = raw.country_code
        if place.price_tier is None and raw.price_tier:
            place.price_tier = raw.price_tier
        if not place.hours and raw.hours:
            place.hours = raw.hours
        if place.category == PlaceCategory.OTHER and raw.category != PlaceCategory.OTHER:
            place.category = raw.category

    def _flag(
        self,
        left: Place,
        right: Place,
        name_sim: float,
        distance_m: float,
        confidence: float,
        anchored: bool = False,
    ) -> None:
        pair = tuple(sorted([str(left.id), str(right.id)]))
        exists = self.session.execute(
            select(MergeReview.id).where(
                MergeReview.left_place_id == pair[0], MergeReview.right_place_id == pair[1]
            )
        ).first()
        if exists:
            return
        self.session.add(
            MergeReview(
                left_place_id=pair[0],
                right_place_id=pair[1],
                decision=MergeDecision.FLAGGED,
                name_similarity=name_sim,
                distance_m=distance_m,
                confidence=confidence,
                reason=(
                    f"{left.name!r} vs {right.name!r}: no merge rule matched "
                    f"(name_sim={name_sim:.2f}, distance={distance_m:.0f}m, "
                    f"shared_distinctive_token={anchored}) but confidence "
                    f"{confidence:.2f} >= {REVIEW_CONFIDENCE}"
                ),
            )
        )
        log.info("flagged possible duplicate: %r <-> %r (conf=%.2f)", left.name, right.name, confidence)
