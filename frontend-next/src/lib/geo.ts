import type { Coords } from '../fixtures/madrid'

/** Great-circle distance in metres. Used only to decide what is "nearby". */
export function metresBetween(a: Coords, b: Coords): number {
  const R = 6_371_000
  const toRad = (d: number) => (d * Math.PI) / 180
  const dLat = toRad(b.lat - a.lat)
  const dLng = toRad(b.lng - a.lng)
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}

export function walkMinutes(metres: number): number {
  return Math.max(1, Math.round(metres / 80)) // ~4.8 km/h
}
