import type { Itinerary } from "../types";
import { CATEGORY_EMOJI, DAY_COLORS, WEEKDAYS } from "../constants";

interface Props {
  itinerary: Itinerary | null;
  visibleDays: Set<number>;
  toggleDay: (index: number) => void;
  loading: boolean;
}

const fmt = (m: number) => {
  const mins = Math.round(m);
  const h = Math.floor(mins / 60);
  return h ? `${h}h ${String(mins % 60).padStart(2, "0")}m` : `${mins}m`;
};

export default function ItineraryPanel({ itinerary, visibleDays, toggleDay, loading }: Props) {
  if (loading) return <div className="muted">Optimizing…</div>;
  if (!itinerary)
    return (
      <div className="muted">
        Set your dates and preferences above, then generate a plan. Days are
        clustered geographically and ordered to minimise travel, with opening
        hours as hard constraints.
      </div>
    );

  return (
    <>
      <div className="muted" style={{ marginBottom: 12 }}>
        <strong style={{ color: "var(--text)" }}>{itinerary.total_stops} stops</strong> ·{" "}
        {fmt(itinerary.total_travel_minutes)} travelling · {itinerary.travel_mode} ·{" "}
        {itinerary.pool_size} candidates considered
      </div>

      {itinerary.days.map((day) => {
        const color = DAY_COLORS[day.day_index % DAY_COLORS.length];
        const on = visibleDays.has(day.day_index);
        return (
          <div className="day-card" key={day.day_index} style={{ opacity: on ? 1 : 0.5 }}>
            <div
              className="day-head"
              style={{ borderLeftColor: color }}
              onClick={() => toggleDay(day.day_index)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === "Enter" && toggleDay(day.day_index)}
            >
              <input type="checkbox" checked={on} readOnly style={{ width: 14, flex: "0 0 14px" }} />
              <span className="t">
                Day {day.day_index + 1} · {WEEKDAYS[day.weekday]}
              </span>
              <span className="meta">
                {day.stops.length} stops · {fmt(day.travel_minutes)}
              </span>
            </div>
            {on && (
              <div className="day-body">
                {day.stops.length === 0 && (
                  <div className="muted" style={{ padding: "6px 0" }}>
                    Nothing could be scheduled — everything in this cluster is
                    closed on {WEEKDAYS[day.weekday]}.
                  </div>
                )}
                {day.stops.map((stop) => (
                  <div key={stop.place_id}>
                    {stop.free_minutes > 0 && (
                      <div className="leg">
                        ⋯ {fmt(stop.free_minutes)} free — next stop opens at{" "}
                        {stop.start_time.slice(0, 5)}
                      </div>
                    )}
                    {stop.free_minutes === 0 && stop.travel_minutes_from_previous > 0 && (
                      <div className="leg">
                        ↓ {fmt(stop.travel_minutes_from_previous)}{" "}
                        {itinerary.travel_mode}
                        {stop.wait_minutes > 0 ? `, wait ${stop.wait_minutes}m` : ""}
                      </div>
                    )}
                    <div className="stop">
                      <div className="idx" style={{ background: color }}>
                        {stop.position}
                      </div>
                      <div className="body">
                        <div className="nm">
                          {CATEGORY_EMOJI[stop.category]} {stop.name}
                        </div>
                        <div className="tm mono">
                          {stop.start_time.slice(0, 5)}–{stop.end_time.slice(0, 5)}
                          {stop.crosses_midnight ? " +1d" : ""}
                          {stop.price_tier ? ` · ${"€".repeat(stop.price_tier)}` : ""}
                          {stop.composite_score != null
                            ? ` · ${stop.composite_score.toFixed(0)}`
                            : ""}
                          {stop.hours_assumed ? " · hours unconfirmed" : ""}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}

      {itinerary.not_scheduled.length > 0 && (
        <details style={{ marginTop: 10 }}>
          <summary className="muted" style={{ cursor: "pointer" }}>
            {itinerary.not_scheduled.length} places not scheduled — why
          </summary>
          <div className="muted" style={{ marginTop: 7, fontSize: 11.5 }}>
            {itinerary.not_scheduled.slice(0, 25).map((d, i) => (
              <div key={i} style={{ padding: "2px 0" }}>
                <strong style={{ color: "var(--text)" }}>{d.name}</strong> — {d.reason}
              </div>
            ))}
          </div>
        </details>
      )}
    </>
  );
}
