"""Human-readable score explanation — the demo surface for the scoring model.

`roam explain "Museo del Prado"` prints exactly why a place scored what it
scored: every source's raw rating, how shrinkage and z-scoring transformed it,
each weight component, and how much corroboration contributed. The point is
that the ranking is defensible line by line rather than a black-box number.
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.ingestion.normalize import normalize_name
from app.models import Place
from app.scoring.model import ScoreResult
from app.scoring.pipeline import measure_distributions, score_one


def explain_place_by_name(
    session: Session, city: str, name: str
) -> tuple[Place, ScoreResult] | None:
    """Fuzzy-find a place by name and score it, live."""
    key = normalize_name(name)
    stmt = (
        select(Place)
        .options(selectinload(Place.signals))
        .where(
            Place.city == city,
            Place.duplicate_of.is_(None),
            or_(
                Place.name_normalized == key,
                Place.name.ilike(f"%{name}%"),
                func.similarity(Place.name_normalized, key) > 0.3,
            ),
        )
        .order_by(func.similarity(Place.name_normalized, key).desc())
        .limit(1)
    )
    place = session.execute(stmt).scalars().first()
    if place is None:
        return None
    distributions = measure_distributions(session, city)
    return place, score_one(place, distributions)


def format_explanation(place: Place, result: ScoreResult) -> str:
    lines: list[str] = []
    rule = "─" * 78

    lines.append(rule)
    lines.append(f"{place.name}   [{place.category.value}]")
    detail = [f"price tier {place.price_tier}" if place.price_tier else "price unknown"]
    if place.neighborhood:
        detail.append(place.neighborhood)
    lines.append(f"  {place.city} · {' · '.join(detail)}")
    if place.tags:
        lines.append(f"  tags: {', '.join(sorted(place.tags))}")
    lines.append(rule)
    lines.append(f"COMPOSITE SCORE: {result.composite_score:.1f} / 100")
    lines.append("")

    for signal in result.signals:
        share = signal.weight / sum(s.weight for s in result.signals) if result.signals else 0
        lines.append(
            f"  {signal.source.value:<15} quality {signal.quality:.3f}   "
            f"weight {signal.weight:.3f}  ({share:.0%} of total)"
        )
        lines.append(
            f"    {'weight =':<10} credibility {signal.credibility:.2f}"
            f" × volume {signal.volume_weight:.2f}"
            f" × recency {signal.recency_weight:.2f}"
            + (f"   [{signal.age_days:.0f} days old]" if signal.age_days is not None else "")
        )
        for note in signal.notes:
            lines.append(f"    · {note}")
        lines.append("")

    lines.append("  How it combines:")
    lines.append(
        f"    weighted mean quality            {result.raw_base:.4f}"
        f"   ({result.source_count} source(s))"
    )
    lines.append(
        f"    total evidence (Σ weights)       {result.evidence:.4f}"
        f"   -> confidence {result.evidence_confidence:.2f}"
    )
    lines.append(
        f"    shrunk toward neutral 0.50       {result.base:.4f}"
        f"   (thin evidence reads as unremarkable)"
    )
    if result.source_count > 1:
        lines.append(
            f"    cross-source agreement           {result.agreement:.4f}"
            f"   (1.0 = sources concur)"
        )
        lines.append(f"    corroboration bonus             +{result.corroboration_bonus:.4f}")
    else:
        lines.append("    corroboration bonus             +0.0000   (single source, no bonus)")
    lines.append(
        f"    composite = min(1, base + bonus) × 100 = {result.composite_score:.1f}"
    )
    lines.append(rule)
    return "\n".join(lines)


def print_explanation(place: Place, result: ScoreResult) -> None:
    print(format_explanation(place, result))
