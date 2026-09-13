import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchFacets, fetchPlaces, generateItinerary } from "./api";
import type { Facets, Itinerary, Place } from "./types";
import { DAY_COLORS, RADIUS_PRESETS, SOURCE_LABELS, isFilterTag } from "./constants";
import MapView from "./components/MapView";
import ItineraryPanel from "./components/ItineraryPanel";
import ScoreDrawer from "./components/ScoreDrawer";

type Tab = "explore" | "plan";

interface Provenance {
  city: string;
  routing_provider: string;
  travel_mode: string;
  sources: { source: string; mode: string; is_live: boolean; signals_in_db: number }[];
}

const today = () => new Date().toISOString().slice(0, 10);

export default function App() {
  const [tab, setTab] = useState<Tab>("explore");
  const [facets, setFacets] = useState<Facets | null>(null);
  const [provenance, setProvenance] = useState<Provenance | null>(null);
  const [places, setPlaces] = useState<Place[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Explore filters
  const [categories, setCategories] = useState<string[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [maxPrice, setMaxPrice] = useState(4);
  const [minScore, setMinScore] = useState(0);
  const [minSources, setMinSources] = useState(1);
  const [radiusM, setRadiusM] = useState(0);

  // Plan controls
  const [startDate, setStartDate] = useState(today());
  const [days, setDays] = useState(3);
  const [pace, setPace] = useState("moderate");
  const [itinerary, setItinerary] = useState<Itinerary | null>(null);
  const [planning, setPlanning] = useState(false);
  const [visibleDays, setVisibleDays] = useState<Set<number>>(new Set());

  const [explainId, setExplainId] = useState<string | null>(null);

  useEffect(() => {
    fetchFacets().then(setFacets).catch((e) => setError(String(e)));
    fetch("/api/meta/provenance")
      .then((r) => r.json())
      .then(setProvenance)
      .catch(() => undefined);
  }, []);

  const center = facets?.center ?? { lat: 40.4168, lng: -3.7038 };

  useEffect(() => {
    if (!facets) return;
    fetchPlaces({
      categories,
      tags,
      maxPriceTier: maxPrice < 4 ? maxPrice : null,
      minScore: minScore > 0 ? minScore : null,
      minSources,
      radius: radiusM > 0 ? { ...center, radiusM } : null,
    })
      .then(setPlaces)
      .catch((e) => setError(String(e)));
  }, [facets, categories, tags, maxPrice, minScore, minSources, radiusM, center.lat, center.lng]);

  const toggle = (list: string[], set: (v: string[]) => void, value: string) =>
    set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);

  const plan = useCallback(async () => {
    setPlanning(true);
    setError(null);
    try {
      const result = await generateItinerary({
        start_date: startDate,
        days,
        pace,
        tags,
        max_price_tier: maxPrice,
      });
      setItinerary(result);
      setVisibleDays(new Set(result.days.map((d) => d.day_index)));
      setTab("plan");
    } catch (e) {
      setError(String(e));
    } finally {
      setPlanning(false);
    }
  }, [startDate, days, pace, tags, maxPrice]);

  const toggleDay = (index: number) =>
    setVisibleDays((prev) => {
      const next = new Set(prev);
      next.has(index) ? next.delete(index) : next.add(index);
      return next;
    });

  const filterTags = useMemo(
    () => (facets?.tags ?? []).filter((t) => isFilterTag(t.value)).slice(0, 22),
    [facets]
  );

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <h1>Roam</h1>
          <div className="sub">
            {facets ? `${facets.city} · ${facets.place_count} places` : "loading…"}
            {provenance && (
              <>
                {" · "}
                {provenance.sources.filter((s) => s.is_live).length}/
                {provenance.sources.length} sources live
              </>
            )}
          </div>
        </div>

        <div className="tabs">
          <button className={tab === "explore" ? "active" : ""} onClick={() => setTab("explore")}>
            Explore
          </button>
          <button className={tab === "plan" ? "active" : ""} onClick={() => setTab("plan")}>
            Itinerary
          </button>
        </div>

        <div className="scroll">
          {error && <div className="banner err">{error}</div>}

          {tab === "explore" && facets && (
            <>
              <div className="section">
                <label>Category</label>
                <div className="chips">
                  {facets.categories.map((c) => (
                    <span
                      key={c.value}
                      className={`chip ${categories.includes(c.value) ? "on" : ""}`}
                      onClick={() => toggle(categories, setCategories, c.value)}
                    >
                      {c.value}
                      <span className="n">{c.count}</span>
                    </span>
                  ))}
                </div>
              </div>

              <div className="section">
                <label>Tags (auto-generated from review text)</label>
                <div className="chips">
                  {filterTags.map((t) => (
                    <span
                      key={t.value}
                      className={`chip ${tags.includes(t.value) ? "on" : ""}`}
                      onClick={() => toggle(tags, setTags, t.value)}
                    >
                      {t.value}
                      <span className="n">{t.count}</span>
                    </span>
                  ))}
                </div>
              </div>

              <div className="section">
                <label>Max price tier · {"€".repeat(maxPrice)}</label>
                <input
                  type="range"
                  min={1}
                  max={4}
                  value={maxPrice}
                  onChange={(e) => setMaxPrice(Number(e.target.value))}
                />
              </div>

              <div className="section">
                <label>Minimum composite score · {minScore}</label>
                <input
                  type="range"
                  min={0}
                  max={80}
                  step={5}
                  value={minScore}
                  onChange={(e) => setMinScore(Number(e.target.value))}
                />
              </div>

              <div className="section">
                <label>Corroboration — sources that must agree</label>
                <div className="chips">
                  {[1, 2, 3, 4].map((n) => (
                    <span
                      key={n}
                      className={`chip ${minSources === n ? "on" : ""}`}
                      onClick={() => setMinSources(n)}
                    >
                      {n === 1 ? "any" : `${n}+`}
                    </span>
                  ))}
                </div>
              </div>

              <div className="section">
                <label>Radius from city centre</label>
                <div className="chips">
                  {RADIUS_PRESETS.map((r) => (
                    <span
                      key={r.label}
                      className={`chip ${radiusM === r.metres ? "on" : ""}`}
                      onClick={() => setRadiusM(r.metres)}
                    >
                      {r.label}
                    </span>
                  ))}
                </div>
              </div>

              <div className="muted">
                Showing {places.length} place{places.length === 1 ? "" : "s"}. Pin size
                encodes composite score.
              </div>

              {provenance && (
                <div className="section" style={{ marginTop: 20 }}>
                  <div className="section-title">Data provenance</div>
                  <div className="prov">
                    {provenance.sources.map((s) => (
                      <div className="r" key={s.source}>
                        <span
                          className="dot"
                          style={{ background: s.is_live ? "var(--ok)" : "var(--warn)" }}
                        />
                        <span>
                          {SOURCE_LABELS[s.source] ?? s.source} — {s.mode} ·{" "}
                          {s.signals_in_db} signals
                        </span>
                      </div>
                    ))}
                    <div style={{ marginTop: 6 }}>
                      Routing: {provenance.routing_provider} ({provenance.travel_mode})
                    </div>
                  </div>
                </div>
              )}
            </>
          )}

          {tab === "plan" && (
            <>
              <div className="section">
                <div className="row">
                  <div>
                    <label>Start date</label>
                    <input
                      type="date"
                      value={startDate}
                      onChange={(e) => setStartDate(e.target.value)}
                    />
                  </div>
                  <div>
                    <label>Days</label>
                    <input
                      type="number"
                      min={1}
                      max={14}
                      value={days}
                      onChange={(e) => setDays(Number(e.target.value))}
                    />
                  </div>
                </div>
              </div>

              <div className="section">
                <label>Pace</label>
                <select value={pace} onChange={(e) => setPace(e.target.value)}>
                  <option value="relaxed">Relaxed — 4 stops/day</option>
                  <option value="moderate">Moderate — 6 stops/day</option>
                  <option value="packed">Packed — 8 stops/day</option>
                </select>
              </div>

              <div className="section">
                <label>
                  Preferences {tags.length > 0 ? `· ${tags.join(", ")}` : "· none selected"}
                </label>
                <div className="muted" style={{ fontSize: 11.5 }}>
                  Tags boost ranking rather than filtering, so a place without
                  the tag can still make the cut on merit. Set them on the
                  Explore tab. Budget: {"€".repeat(maxPrice)}.
                </div>
              </div>

              <div className="section">
                <button className="primary" onClick={plan} disabled={planning}>
                  {planning ? "Optimizing…" : "Generate itinerary"}
                </button>
              </div>

              <ItineraryPanel
                itinerary={itinerary}
                visibleDays={visibleDays}
                toggleDay={toggleDay}
                loading={planning}
              />
            </>
          )}
        </div>
      </aside>

      <div className="map-wrap">
        <MapView
          center={center}
          places={places}
          itinerary={itinerary}
          visibleDays={visibleDays}
          radiusM={radiusM}
          onExplain={setExplainId}
        />

        {itinerary && itinerary.days.length > 0 && (
          <div className="legend">
            <h4>Days — click to toggle</h4>
            {itinerary.days.map((day) => (
              <div
                className={`legend-row ${visibleDays.has(day.day_index) ? "" : "off"}`}
                key={day.day_index}
                onClick={() => toggleDay(day.day_index)}
              >
                <span
                  className="sw"
                  style={{ background: DAY_COLORS[day.day_index % DAY_COLORS.length] }}
                />
                <span>
                  Day {day.day_index + 1} — {day.stops.length} stops
                </span>
              </div>
            ))}
          </div>
        )}

        {explainId && <ScoreDrawer placeId={explainId} onClose={() => setExplainId(null)} />}
      </div>
    </div>
  );
}
