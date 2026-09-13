import type { Facets, Itinerary, Place, ScoreBreakdown } from "./types";

// Relative URLs: Vite proxies /api to the backend in dev, so there is no API
// base URL to configure and no CORS preflight in the common path.
const BASE = "/api";

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json() as Promise<T>;
}

export const fetchFacets = () => get<Facets>("/facets");

export interface PlaceQuery {
  categories?: string[];
  tags?: string[];
  maxPriceTier?: number | null;
  minScore?: number | null;
  minSources?: number | null;
  radius?: { lat: number; lng: number; radiusM: number } | null;
}

export function fetchPlaces(query: PlaceQuery): Promise<Place[]> {
  const params = new URLSearchParams();
  query.categories?.forEach((c) => params.append("category", c));
  query.tags?.forEach((t) => params.append("tag", t));
  if (query.maxPriceTier) params.set("max_price_tier", String(query.maxPriceTier));
  if (query.minScore) params.set("min_score", String(query.minScore));
  if (query.minSources && query.minSources > 1)
    params.set("min_sources", String(query.minSources));
  if (query.radius) {
    params.set("lat", String(query.radius.lat));
    params.set("lng", String(query.radius.lng));
    params.set("radius_m", String(query.radius.radiusM));
  }
  return get<Place[]>(`/places?${params.toString()}`);
}

export const fetchScore = (placeId: string) =>
  get<ScoreBreakdown>(`/places/${placeId}/score`);

export interface ItineraryQuery {
  start_date: string;
  days: number;
  pace: string;
  tags: string[];
  max_price_tier: number;
  center_lat?: number;
  center_lng?: number;
  radius_m?: number;
}

export async function generateItinerary(query: ItineraryQuery): Promise<Itinerary> {
  const response = await fetch(`${BASE}/itinerary`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(query),
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json() as Promise<Itinerary>;
}
