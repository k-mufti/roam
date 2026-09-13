import { useEffect, useMemo } from "react";
import L from "leaflet";
import {
  Circle, CircleMarker, MapContainer, Marker, Pane, Polyline, Popup, TileLayer, useMap,
} from "react-leaflet";
import type { Itinerary, Place } from "../types";
import { CATEGORY_EMOJI, DAY_COLORS } from "../constants";

interface Props {
  center: { lat: number; lng: number };
  places: Place[];
  itinerary: Itinerary | null;
  visibleDays: Set<number>;
  radiusM: number;
  onExplain: (placeId: string) => void;
}

/** Cache icons by (number, colour) — Leaflet recreates the DOM node otherwise. */
const iconCache = new Map<string, L.DivIcon>();

function numberIcon(position: number, color: string): L.DivIcon {
  const key = `${position}-${color}`;
  const cached = iconCache.get(key);
  if (cached) return cached;
  const icon = L.divIcon({
    className: "stop-pin-wrap",
    html: `<div class="stop-pin" style="background:${color}">${position}</div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    popupAnchor: [0, -12],
  });
  iconCache.set(key, icon);
  return icon;
}

/**
 * Keep Leaflet's idea of the container size in sync with the actual element.
 *
 * Leaflet measures its container once at init. Here the map lives in a flex
 * child that is still settling at that moment, so it latched onto a smaller
 * box: tiles stopped short of the pane edge and `fitBounds` computed its zoom
 * against the wrong dimensions, leaving routes hanging off-screen.
 */
function KeepSized() {
  const map = useMap();
  useEffect(() => {
    const container = map.getContainer();
    let frame = 0;
    // Deferred to the next frame: Leaflet's stylesheet is imported as an ES
    // module, so on first paint the container can still be unstyled and measure
    // 0. Calling invalidateSize synchronously here just re-latches the wrong
    // size, and (because Leaflet recentres the map pane on resize) leaves the
    // pane translated by half the container.
    const sync = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => map.invalidateSize({ animate: false }));
    };
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(container);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [map]);
  return null;
}

/** Refit the viewport when the plotted geometry changes, not on every render. */
function FitBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  const key = JSON.stringify(points);
  useEffect(() => {
    if (points.length === 0) return;
    if (points.length === 1) {
      map.setView(points[0], 15);
      return;
    }
    // Deferred for the same reason as KeepSized: fitBounds derives its zoom
    // from the container size, so fitting before the size is settled produces
    // a wrong zoom rather than a wrong centre.
    const frame = requestAnimationFrame(() =>
      map.fitBounds(points, { padding: [60, 60], maxZoom: 15, animate: false })
    );
    return () => cancelAnimationFrame(frame);
    // Keyed on the geometry itself so panning the map does not snap it back.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, map]);
  return null;
}

export default function MapView({
  center, places, itinerary, visibleDays, radiusM, onExplain,
}: Props) {
  const shownDays = useMemo(
    () => (itinerary?.days ?? []).filter((d) => visibleDays.has(d.day_index) && d.stops.length > 0),
    [itinerary, visibleDays]
  );

  /** Place ids that appear in a visible day, so the base pin can be suppressed
   *  and the numbered itinerary marker drawn instead. */
  const routed = useMemo(() => {
    const ids = new Set<string>();
    shownDays.forEach((d) => d.stops.forEach((s) => ids.add(s.place_id)));
    return ids;
  }, [shownDays]);

  const fitPoints = useMemo<[number, number][]>(() => {
    if (shownDays.length > 0) {
      return shownDays.flatMap((d) => d.stops.map((s) => [s.lat, s.lng] as [number, number]));
    }
    return places.slice(0, 300).map((p) => [p.lat, p.lng] as [number, number]);
  }, [shownDays, places]);

  return (
    <MapContainer center={[center.lat, center.lng]} zoom={13} scrollWheelZoom>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        maxZoom={19}
      />
      <KeepSized />
      <FitBounds points={fitPoints} />

      {radiusM > 0 && (
        <Circle
          center={[center.lat, center.lng]}
          radius={radiusM}
          pathOptions={{ color: "#ff6b4a", weight: 1.5, fillOpacity: 0.05, dashArray: "6 6" }}
        />
      )}

      {/* Candidate pins. Radius encodes composite score so the map itself
          communicates the ranking rather than treating every place alike. */}
      {places.map((place) => {
        if (routed.has(place.id)) return null;
        const score = place.composite_score ?? 40;
        return (
          <CircleMarker
            key={place.id}
            center={[place.lat, place.lng]}
            radius={3 + Math.max(0, (score - 30) / 16)}
            pathOptions={{
              color: "#8fa0bb",
              weight: 1,
              fillColor: "#6f83a3",
              fillOpacity: 0.5,
            }}
          >
            <Popup>
              <PlacePopup place={place} onExplain={onExplain} />
            </Popup>
          </CircleMarker>
        );
      })}

      {/* One polyline per visible day, in that day's colour. */}
      {shownDays.map((day) => (
        <Polyline
          key={`line-${day.day_index}`}
          positions={day.stops.map((s) => [s.lat, s.lng] as [number, number])}
          pathOptions={{
            color: DAY_COLORS[day.day_index % DAY_COLORS.length],
            weight: 4,
            opacity: 0.9,
          }}
        />
      ))}

      {/* Numbered stop markers, in their own pane so they always sit above the
          route lines and the candidate pins. A divIcon rather than a
          CircleMarker because the visiting order is the whole point of the
          route, and an SVG circle cannot carry a label. */}
      <Pane name="stops" style={{ zIndex: 640 }}>
      {shownDays.map((day) =>
        day.stops.map((stop) => {
          const color = DAY_COLORS[day.day_index % DAY_COLORS.length];
          const place = places.find((p) => p.id === stop.place_id);
          return (
            <Marker
              key={`stop-${day.day_index}-${stop.place_id}`}
              position={[stop.lat, stop.lng]}
              pane="stops"
              icon={numberIcon(stop.position, color)}
            >
              <Popup>
                <div className="pop">
                  <h3>
                    {stop.position}. {stop.name}
                  </h3>
                  <div className="meta">
                    Day {day.day_index + 1} · {stop.start_time.slice(0, 5)}–
                    {stop.end_time.slice(0, 5)}
                    {stop.crosses_midnight ? " (+1d)" : ""} · {stop.category}
                    {stop.hours_assumed ? " · hours unconfirmed" : ""}
                  </div>
                  {place && <PlacePopup place={place} onExplain={onExplain} compact />}
                </div>
              </Popup>
            </Marker>
          );
        })
      )}
      </Pane>
    </MapContainer>
  );
}

function PlacePopup({
  place, onExplain, compact = false,
}: { place: Place; onExplain: (id: string) => void; compact?: boolean }) {
  return (
    <div className="pop">
      {!compact && (
        <>
          <h3>
            {CATEGORY_EMOJI[place.category]} {place.name}
          </h3>
          <div className="meta">
            {place.category}
            {place.price_tier ? ` · ${"€".repeat(place.price_tier)}` : ""} ·{" "}
            score {place.composite_score?.toFixed(0) ?? "—"} · {place.source_count} source
            {place.source_count === 1 ? "" : "s"}
          </div>
        </>
      )}
      <div>
        {place.tags
          .filter((t) => !t.startsWith("category:"))
          .slice(0, 6)
          .map((t) => (
            <span className="tag" key={t}>
              {t.replace("topic:", "#")}
            </span>
          ))}
      </div>
      <button onClick={() => onExplain(place.id)}>Why this score?</button>
    </div>
  );
}
