"""CLI rendering for an itinerary. Presentation only — no logic lives here."""

from __future__ import annotations

from app.optimizer.itinerary import Itinerary

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _fmt_minutes(total: float) -> str:
    minutes = int(round(total))
    hours, rest = divmod(minutes, 60)
    return f"{hours}h {rest:02d}m" if hours else f"{rest}m"


def format_itinerary(itinerary: Itinerary, *, max_unused: int = 8) -> str:
    lines: list[str] = []
    rule = "═" * 82
    thin = "─" * 82

    lines.append(rule)
    lines.append(
        f"{itinerary.city} · {len(itinerary.days)} day(s) from {itinerary.start_date} "
        f"· {itinerary.pace.value} pace"
    )
    detail = [
        f"travel: {itinerary.travel_mode} via {itinerary.provider_name}",
        f"pool: {itinerary.pool_size} candidates",
        f"budget: tier ≤{itinerary.max_price_tier}",
    ]
    if itinerary.requested_tags:
        detail.append(f"preferred: {', '.join(itinerary.requested_tags)}")
    lines.append("  " + " · ".join(detail))
    lines.append(rule)

    for day in itinerary.days:
        header = (
            f"Day {day.day_index + 1} — {DAY_NAMES[day.weekday]} {day.day_date.isoformat()}"
        )
        lines.append("")
        lines.append(header)
        lines.append(thin)
        if not day.stops:
            lines.append("  (nothing could be scheduled)")
        for position, scheduled in enumerate(day.stops, start=1):
            stop = scheduled.stop
            if scheduled.free_minutes:
                lines.append(
                    f"        ⋯ {_fmt_minutes(scheduled.free_minutes)} free "
                    f"(next stop does not open until {scheduled.start_time:%H:%M})"
                )
            elif scheduled.travel_seconds:
                wait = (
                    f", wait {scheduled.wait_minutes}m" if scheduled.wait_minutes else ""
                )
                lines.append(
                    f"        ↓ {_fmt_minutes(scheduled.travel_seconds / 60)} "
                    f"{itinerary.travel_mode}{wait}"
                )
            marker = "+1d" if scheduled.crosses_midnight else "   "
            lines.append(
                f"  {position}. {scheduled.start_time:%H:%M}–{scheduled.end_time:%H:%M}{marker} "
                f"{stop.name}"
            )
            facets = [stop.category.value]
            if stop.price_tier:
                facets.append("€" * stop.price_tier)
            facets.append(f"score {stop.score:.0f}")
            chips = [
                t for t in stop.tags if not t.startswith(("topic:", "category:"))
            ][:5]
            if chips:
                facets.append(", ".join(chips))
            lines.append(f"        {' · '.join(facets)}")

        lines.append(
            f"  ── {len(day.stops)} stop(s) · "
            f"{_fmt_minutes(day.total_travel_seconds / 60)} travelling · "
            f"{_fmt_minutes(day.total_dwell_minutes)} at stops"
            + (f" · {_fmt_minutes(day.total_free_minutes)} free" if day.total_free_minutes else "")
        )

    lines.append("")
    lines.append(rule)
    lines.append(
        f"Totals: {itinerary.total_stops} stops · "
        f"{_fmt_minutes(itinerary.total_travel_minutes)} travelling"
    )
    if itinerary.unused:
        lines.append("")
        lines.append(f"Not scheduled ({len(itinerary.unused)}):")
        for stop, reason in itinerary.unused[:max_unused]:
            lines.append(f"  · {stop.name} — {reason}")
        if len(itinerary.unused) > max_unused:
            lines.append(f"  · ... and {len(itinerary.unused) - max_unused} more")
    lines.append(rule)
    return "\n".join(lines)


def print_itinerary(itinerary: Itinerary) -> None:
    print(format_itinerary(itinerary))
