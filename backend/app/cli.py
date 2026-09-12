"""`trip` — the operator CLI.

Deliberately a plain argparse script with no scheduler. The spec calls for
"a script/CLI runner is fine, no Kafka/Celery": ingestion here is a batch job
over a few thousand rows that takes seconds, so a queue would be pure ceremony.
The seam for adding one later is `app.ingestion.runner.run_source`, which is
already a pure function of (session, source, city).
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import func, select

from app.config import get_settings
from app.db import session_scope
from app.models import MergeReview, Place, SourceName, SourceSignal


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )


# --- commands ---------------------------------------------------------------


def cmd_ingest(args: argparse.Namespace) -> int:
    from app.ingestion.runner import SOURCE_ORDER, run_all

    settings = get_settings()
    city = settings.city
    sources = (
        tuple(SourceName(s) for s in args.source) if args.source else SOURCE_ORDER
    )

    print(f"Ingesting {city.name} from: {', '.join(s.value for s in sources)}\n")
    with session_scope() as session:
        reports = run_all(
            session,
            settings,
            city,
            sources=sources,
            force_fixtures=args.fixtures,
            refresh=args.refresh,
        )
    print(f"{'source':<15} {'mode':<8} {'':<10}counts")
    print("-" * 96)
    for report in reports:
        print(report.summary())
    return 1 if any(r.error for r in reports) else 0


def cmd_score(args: argparse.Namespace) -> int:
    from app.scoring.pipeline import rescore_city

    settings = get_settings()
    with session_scope() as session:
        n = rescore_city(session, settings.city.name)
    print(f"Scored {n} places in {settings.city.name}.")
    return 0


def cmd_tag(args: argparse.Namespace) -> int:
    from app.tagging.pipeline import retag_city

    settings = get_settings()
    with session_scope() as session:
        n = retag_city(session, settings.city.name)
    print(f"Tagged {n} places in {settings.city.name}.")
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    from app.scoring.explain import explain_place_by_name, print_explanation

    settings = get_settings()
    with session_scope() as session:
        result = explain_place_by_name(session, settings.city.name, args.name)
        if result is None:
            print(f"No place matching {args.name!r} in {settings.city.name}.", file=sys.stderr)
            return 1
        print_explanation(*result)
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """Print the entity-resolution decisions that were too close to call."""
    with session_scope() as session:
        rows = list(
            session.execute(
                select(MergeReview).where(MergeReview.resolved.is_(False)).order_by(
                    MergeReview.confidence.desc()
                )
            ).scalars()
        )
        if not rows:
            print("No ambiguous merges pending review.")
            return 0
        print(f"{len(rows)} ambiguous merge(s) awaiting review:\n")
        for row in rows:
            print(
                f"  conf={row.confidence:.2f}  name_sim={row.name_similarity:.2f}  "
                f"dist={row.distance_m:.0f}m\n    {row.reason}\n"
            )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    settings = get_settings()
    city = settings.city.name
    with session_scope() as session:
        total = session.scalar(
            select(func.count()).select_from(Place).where(Place.city == city)
        )
        canonical = session.scalar(
            select(func.count())
            .select_from(Place)
            .where(Place.city == city, Place.duplicate_of.is_(None))
        )
        scored = session.scalar(
            select(func.count())
            .select_from(Place)
            .where(Place.city == city, Place.composite_score.isnot(None))
        )
        tagged = session.scalar(
            select(func.count())
            .select_from(Place)
            .where(Place.city == city, func.cardinality(Place.tags) > 0)
        )
        by_source = session.execute(
            select(SourceSignal.source, func.count())
            .join(Place, Place.id == SourceSignal.place_id)
            .where(Place.city == city)
            .group_by(SourceSignal.source)
        ).all()
        multi = session.scalar(
            select(func.count()).select_from(
                select(SourceSignal.place_id)
                .group_by(SourceSignal.place_id)
                .having(func.count(func.distinct(SourceSignal.source)) > 1)
                .subquery()
            )
        )
        pending = session.scalar(
            select(func.count()).select_from(MergeReview).where(MergeReview.resolved.is_(False))
        )

    print(f"City:                {city}")
    print(f"Places:              {total} ({canonical} canonical)")
    print(f"Scored:              {scored}")
    print(f"Tagged:              {tagged}")
    print(f"Corroborated:        {multi} place(s) with signals from >1 source")
    print(f"Pending merge review:{pending:>4}")
    print("Signals by source:")
    for source, count in sorted(by_source, key=lambda r: -r[1]):
        print(f"  {source.value:<16} {count}")
    return 0


def cmd_itinerary(args: argparse.Namespace) -> int:
    from datetime import date

    from app.models import Pace
    from app.optimizer.itinerary import build_itinerary
    from app.optimizer.presenter import print_itinerary

    settings = get_settings()
    start = date.fromisoformat(args.start)
    with session_scope() as session:
        plan = build_itinerary(
            session,
            city=settings.city,
            start_date=start,
            days=args.days,
            pace=Pace(args.pace),
            tags=tuple(args.tag or ()),
            max_price_tier=args.budget,
        )
    print_itinerary(plan)
    return 0


# --- parser -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trip", description="Trip Package operator CLI")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="Run ingestion for one or more sources")
    p.add_argument(
        "--source",
        action="append",
        choices=[s.value for s in SourceName],
        help="Repeatable. Omit to run every source in dependency order.",
    )
    p.add_argument(
        "--fixtures",
        action="store_true",
        help="Force fixture mode for every source, ignoring configured keys.",
    )
    p.add_argument("--refresh", action="store_true", help="Bypass the on-disk response cache.")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("score", help="Recompute composite scores")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("tag", help="Regenerate tags from text evidence")
    p.set_defaults(func=cmd_tag)

    p = sub.add_parser("explain", help="Show a place's score breakdown")
    p.add_argument("name", help="Place name (fuzzy match)")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("review", help="List ambiguous merges needing a human decision")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("status", help="Summarize what's in the database")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("itinerary", help="Generate an itinerary from the CLI")
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--pace", default="moderate", choices=["relaxed", "moderate", "packed"])
    p.add_argument("--tag", action="append", help="Repeatable preferred tag")
    p.add_argument("--budget", type=int, default=4, choices=[1, 2, 3, 4])
    p.set_defaults(func=cmd_itinerary)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
