export type Category =
  | "restaurant" | "cafe" | "bar" | "attraction" | "museum"
  | "park" | "shopping" | "hotel" | "nightlife" | "other";

export interface SourceSignal {
  source: string;
  rating: number | null;
  review_count: number | null;
  url: string | null;
  extra: Record<string, unknown>;
}

export interface Place {
  id: string;
  name: string;
  city: string;
  lat: number;
  lng: number;
  category: Category;
  price_tier: number | null;
  tags: string[];
  hours: Record<string, { open: string; close: string }[]> | null;
  composite_score: number | null;
  address: string | null;
  neighborhood: string | null;
  source_signals: SourceSignal[];
  source_count: number;
}

export interface Facets {
  city: string;
  center: { lat: number; lng: number };
  categories: { value: Category; count: number }[];
  tags: { value: string; count: number }[];
  price_tiers: { value: number; count: number }[];
  score_range: { min: number; max: number };
  place_count: number;
  sources: { value: string; count: number }[];
}

export interface ItineraryStop {
  position: number;
  place_id: string;
  name: string;
  lat: number;
  lng: number;
  category: Category;
  price_tier: number | null;
  tags: string[];
  composite_score: number | null;
  start_time: string;
  end_time: string;
  crosses_midnight: boolean;
  travel_minutes_from_previous: number;
  wait_minutes: number;
  free_minutes: number;
  hours_assumed: boolean;
}

export interface ItineraryDay {
  day_index: number;
  date: string;
  weekday: number;
  stops: ItineraryStop[];
  travel_minutes: number;
  dwell_minutes: number;
  free_minutes: number;
}

export interface Itinerary {
  city: string;
  start_date: string;
  pace: string;
  travel_mode: string;
  routing_provider: string;
  pool_size: number;
  requested_tags: string[];
  max_price_tier: number;
  days: ItineraryDay[];
  total_stops: number;
  total_travel_minutes: number;
  not_scheduled: { name: string; reason: string }[];
}

export interface ScoreBreakdown {
  place_id: string;
  name: string;
  composite_score: number | null;
  breakdown: {
    composite_score: number;
    raw_base: number;
    evidence: number;
    evidence_confidence: number;
    base: number;
    corroboration_bonus: number;
    agreement: number;
    source_count: number;
    model_version: string;
    signals: {
      source: string;
      quality: number;
      weight: number;
      weight_components: { credibility: number; volume: number; recency: number };
      raw_rating: number | null;
      shrunk_rating: number | null;
      z_score: number | null;
      review_count: number | null;
      age_days: number | null;
      notes: string[];
    }[];
  } | null;
}
