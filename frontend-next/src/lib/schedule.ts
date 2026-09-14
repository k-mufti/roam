import type { Hotel, Place } from '../fixtures/madrid'
import { metresBetween, walkMinutes } from './geo'

/** Days start at half past nine. Civilised. */
export const DAY_START_MINS = 9 * 60 + 30

export type ScheduledStop = {
  place: Place
  walkMins: number   // walk from the previous point to this one
  startMins: number
  endMins: number
}

/**
 * Lays the day's stops onto a clock: walk from the hotel to the first stop,
 * stay for its duration, walk to the next. No optimisation — the order is
 * whatever the traveller chose.
 */
export function buildDay(hotel: Hotel, places: Place[]): ScheduledStop[] {
  let cursor = DAY_START_MINS
  let from = hotel.coords

  return places.map((place) => {
    const walk = walkMinutes(metresBetween(from, place.coords))
    const startMins = cursor + walk
    const endMins = startMins + place.durationMins
    cursor = endMins
    from = place.coords
    return { place, walkMins: walk, startMins, endMins }
  })
}

/** Total time out of the hotel, including the walk back. */
export function dayTotals(hotel: Hotel, stops: ScheduledStop[]) {
  if (stops.length === 0) return { walkMins: 0, outMins: 0, endMins: DAY_START_MINS }
  const last = stops[stops.length - 1]
  const walkHome = walkMinutes(metresBetween(last.place.coords, hotel.coords))
  const walkMins = stops.reduce((sum, s) => sum + s.walkMins, 0) + walkHome
  return {
    walkMins,
    outMins: last.endMins + walkHome - DAY_START_MINS,
    endMins: last.endMins + walkHome,
  }
}

export function clock(mins: number): string {
  const h = Math.floor(mins / 60) % 24
  const m = Math.round(mins % 60)
  return `${String(h).padStart(2, '0')}.${String(m).padStart(2, '0')}`
}

export function duration(mins: number): string {
  const h = Math.floor(mins / 60)
  const m = Math.round(mins % 60)
  if (h === 0) return `${m} min`
  if (m === 0) return `${h} hr`
  return `${h} hr ${m} min`
}
