import { emptyTrip, type Screen, type Trip } from '../types'

/**
 * The whole session lives in localStorage so a refresh does not lose the trip.
 * Nothing leaves the browser — there is no backend in this build.
 */

const KEY = 'roam.session.v1'

export type Session = {
  trip: Trip
  screen: Screen
  onboardStep: number
  refineStep: number
}

export const emptySession: Session = {
  trip: emptyTrip,
  screen: 'onboarding',
  onboardStep: 0,
  refineStep: 0,
}

export function loadSession(): Session {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return emptySession
    const parsed = JSON.parse(raw) as Partial<Session>
    /* Merge over the defaults so an older saved shape cannot break the app. */
    return {
      ...emptySession,
      ...parsed,
      trip: { ...emptyTrip, ...(parsed.trip ?? {}) },
    }
  } catch {
    return emptySession
  }
}

export function saveSession(session: Session) {
  try {
    localStorage.setItem(KEY, JSON.stringify(session))
  } catch {
    /* Private browsing, quota, blocked storage — the app still works. */
  }
}

export function clearSession() {
  try {
    localStorage.removeItem(KEY)
  } catch {
    /* ignore */
  }
}
