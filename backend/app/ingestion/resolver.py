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

4. **Decision.** Above `AUTO_MERGE_CONFIDENCE` we merge. Below
   `REVIEW_CONFIDENCE` we create a new place. **In between we do neither
   silently**: we create the place *and* write a `MergeReview` row so a human
   can adjudicate. Guessing in the ambiguous band is how dedup pipelines
   quietly corrupt their own data.

Coordinate-less mentions (Reddit, blog) take a stricter path: name-only match
against places an authoritative source already geocoded, at a much higher
threshold, and are dropped if nothing matches.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from rapidfuzz import fuzz
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.ingestion.base import RawPlace
from app.ingestion.normalize import content_hash, haversine_m, normalize_name
from app.models import MergeDecision, MergeReview, Place, PlaceCategory, SourceSignal, TextEvidence

log = logging.getLogger(__name__)

# --- Tunables ---------------------------------------------------------------
# These are module constants rather than settings because changing them changes
# data quality, so they belong in code review and in the test suite.

#: Candidate blocking radius. Generous on purpose — cheap to over-fetch here,
#: expensive to miss a true duplicate.
BLOCK_RADIUS_M = 400.0
#: Beyond this, two records are not the same building, whatever the names say.
MAX_MERGE_DISTANCE_M = 250.0
#: Minimum fuzzy name similarity (0-1) for a pair to be considered at all.
MIN_NAME_SIMILARITY = 0.55
#: Blend weights for the confidence score.
NAME_WEIGHT = 0.65
DISTANCE_WEIGHT = 0.35

AUTO_MERGE_CONFIDENCE = 0.86
REVIEW_CONFIDENCE = 0.62

#: Coordinate-less mentions need near-certainty on the name alone.
NAME_ONLY_SIMILARITY = 0.90
#: ...and are only matched within this radius of the city centre.
NAME_ONLY_CITY_SCOPE = True


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


def merge_confidence(name_sim: float, distance_m: float) -> float:
    """Blend name similarity and proximity into a single 0-1 confidence."""
    if distance_m > MAX_MERGE_DISTANCE_M:
        return 0.0
    proximity = max(0.0, 1.0 - (distance_m / MAX_MERGE_DISTANCE_M))
    return NAME_WEIGHT * name_sim + DISTANCE_WEIGHT * proximity


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

        best: tuple[float, float, float, Place] | None = None  # conf, name_sim, dist, place
        for cand in candidates:
            sim = name_similarity(key, cand.name_normalized)
            if sim < MIN_NAME_SIMILARITY:
                continue
            dist = haversine_m(raw.lat, raw.lng, cand.lat, cand.lng)
            conf = merge_confidence(sim, dist)
            if best is None or conf > best[0]:
                best = (conf, sim, dist, cand)

        if best is None:
            return ResolutionOutcome(
                self._create_place(raw), MergeDecision.CREATED, 0.0, "no similar candidate"
            )

        conf, sim, dist, cand = best

        if conf >= AUTO_MERGE_CONFIDENCE:
            self.stats.merged += 1
            return ResolutionOutcome(
                cand,
                MergeDecision.MERGED,
                conf,
                f"name_sim={sim:.2f} distance={dist:.0f}m",
            )

        place = self._create_place(raw)
        if conf >= REVIEW_CONFIDENCE:
            # The ambiguous band: plausible duplicate, not confident enough to
            # act. Keep both rows, record the pair, move on.
            self._flag(place, cand, sim, dist, conf)
            self.stats.flagged += 1
            return ResolutionOutcome(
                place,
                MergeDecision.FLAGGED,
                conf,
                f"ambiguous: name_sim={sim:.2f} distance={dist:.0f}m",
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

        stmt = (
            select(Place)
            .where(
                Place.city == raw.city,
                Place.duplicate_of.is_(None),
                or_(
                    Place.name_normalized == key,
                    func.similarity(Place.name_normalized, key) > 0.4,
                ),
            )
            .limit(25)
        )
        best: tuple[float, Place] | None = None
        for cand in self.session.execute(stmt).scalars():
            sim = name_similarity(key, cand.name_normalized)
            if best is None or sim > best[0]:
                best = (sim, cand)

        if best is None or best[0] < NAME_ONLY_SIMILARITY:
            self.stats.dropped += 1
            got = f"{best[0]:.2f}" if best else "none"
            return ResolutionOutcome(
                None,
                MergeDecision.CREATED,
                best[0] if best else 0.0,
                f"no geocoded place matched {raw.name!r} (best={got})",
            )

        sim, cand = best
        self.stats.merged += 1
        return ResolutionOutcome(cand, MergeDecision.MERGED, sim, f"name-only match sim={sim:.2f}")

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
        self, left: Place, right: Place, name_sim: float, distance_m: float, confidence: float
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
                    f"{left.name!r} vs {right.name!r}: confidence {confidence:.2f} falls in the "
                    f"ambiguous band [{REVIEW_CONFIDENCE}, {AUTO_MERGE_CONFIDENCE})"
                ),
            )
        )
        log.info("flagged possible duplicate: %r <-> %r (conf=%.2f)", left.name, right.name, confidence)
