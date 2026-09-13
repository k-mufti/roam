import type { Category } from "./types";

/** Colour per itinerary day. Also used for the route polylines and markers. */
export const DAY_COLORS = [
  "#ff6b4a", "#4ea8ff", "#3ecf8e", "#c77dff", "#f5c451", "#ff8fab", "#64d2d2",
];

export const CATEGORY_EMOJI: Record<Category, string> = {
  restaurant: "🍽", cafe: "☕", bar: "🍸", attraction: "📍", museum: "🏛",
  park: "🌳", shopping: "🛍", hotel: "🛏", nightlife: "🪩", other: "•",
};

/** The spec's "3-5 mile radius view". Stored in metres; labelled in miles. */
export const RADIUS_PRESETS = [
  { label: "off", metres: 0 },
  { label: "1 mi", metres: 1609 },
  { label: "3 mi", metres: 4828 },
  { label: "5 mi", metres: 8047 },
];

export const SOURCE_LABELS: Record<string, string> = {
  google_places: "Google Places",
  yelp: "Yelp Fusion",
  reddit: "Reddit",
  blog: "Wikivoyage",
};

export const WEEKDAYS = [
  "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
];

/** Tags that are namespaced internals, not user-facing filter chips. */
export const isFilterTag = (tag: string) =>
  !tag.startsWith("topic:") && !tag.startsWith("category:");
