import { useMemo, useState } from 'react'
import { Circle, MapContainer, Marker, Polyline, TileLayer } from 'react-leaflet'
import { HOTELS, PLACES, hotelById, placeById, type Place } from '../fixtures/madrid'
import { MapFocus, TILE_ATTR, TILE_URL, pinIcon } from '../components/mapBits'
import TripBar from '../components/TripBar'
import Dialog from '../components/Dialog'
import { VICINITY_METRES } from '../lib/constants'
import { metresBetween, walkMinutes } from '../lib/geo'
import { buildDay, clock, dayTotals, duration } from '../lib/schedule'
import { days as dayLabel, stops as stopLabel } from '../lib/labels'
import type { Trip } from '../types'

type Filter = 'both' | 'food' | 'activity'
type Itinerary = Record<number, string[]>

const GLYPH: Record<Place['kind'], string> = { food: '✦', activity: '❖' }

/**
 * Screen 4 — day tabs, the hotel's neighbourhood on the map, and the
 * nearby places you can add to whichever day is open.
 */
export default function Planner({
  trip,
  setItinerary,
  onFinish,
  onBack,
}: {
  trip: Trip
  setItinerary: (update: (cur: Itinerary) => Itinerary) => void
  onFinish: () => void
  onBack: () => void
}) {
  const [day, setDay] = useState(0)
  const [filter, setFilter] = useState<Filter>('both')
  const [focusId, setFocusId] = useState<string | null>(null)
  const [showOverview, setShowOverview] = useState(false)

  const hotel = hotelById(trip.hotelId ?? '') ?? HOTELS[0]

  /* Everything within a walk of the hotel, nearest first. */
  const nearby = useMemo(
    () =>
      PLACES.map((p) => ({ place: p, metres: metresBetween(hotel.coords, p.coords) }))
        .filter((x) => x.metres <= VICINITY_METRES)
        .sort((a, b) => a.metres - b.metres),
    [hotel],
  )

  const counts = {
    both: nearby.length,
    food: nearby.filter((x) => x.place.kind === 'food').length,
    activity: nearby.filter((x) => x.place.kind === 'activity').length,
  }

  const visible = nearby.filter((x) => filter === 'both' || x.place.kind === filter)

  const dayStops = trip.itinerary[day] ?? []
  const isAdded = (id: string) => dayStops.includes(id)

  /** Which other day, if any, already has this place. */
  const scheduledOn = (id: string): number | null => {
    for (let d = 0; d < trip.days; d++) {
      if (d !== day && (trip.itinerary[d] ?? []).includes(id)) return d
    }
    return null
  }

  /* Functional so that several quick adds cannot overwrite one another. */
  const toggleStop = (id: string) =>
    setItinerary((cur) => {
      const stops = cur[day] ?? []
      return {
        ...cur,
        [day]: stops.includes(id) ? stops.filter((s) => s !== id) : [...stops, id],
      }
    })

  const moveStop = (index: number, delta: number) =>
    setItinerary((cur) => {
      const stops = [...(cur[day] ?? [])]
      const target = index + delta
      if (target < 0 || target >= stops.length) return cur
      ;[stops[index], stops[target]] = [stops[target], stops[index]]
      return { ...cur, [day]: stops }
    })

  const moveToDay = (id: string, toDay: number) =>
    setItinerary((cur) => ({
      ...cur,
      [day]: (cur[day] ?? []).filter((s) => s !== id),
      [toDay]: [...(cur[toDay] ?? []), id],
    }))

  const clearDay = () => setItinerary((cur) => ({ ...cur, [day]: [] }))

  const totalStops = Object.values(trip.itinerary).flat().length

  const stopPlaces = dayStops.map((id) => placeById(id)).filter((p): p is Place => !!p)
  const schedule = buildDay(hotel, stopPlaces)
  const totals = dayTotals(hotel, schedule)

  /* Hotel → stop 1 → stop 2 … drawn in the order they were added. */
  const route: [number, number][] = [
    [hotel.coords.lat, hotel.coords.lng],
    ...stopPlaces.map((p) => [p.coords.lat, p.coords.lng] as [number, number]),
  ]

  /* A package can seed stops outside the walking radius; they still need a
     pin, otherwise the route line runs off to nothing. */
  const pinned = useMemo(() => {
    const byId = new Map(visible.map((x) => [x.place.id, x.place]))
    for (const p of stopPlaces) byId.set(p.id, p)
    return [...byId.values()]
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, dayStops.join(',')])

  const focusCenter: [number, number] = focusId
    ? (() => {
        const p = placeById(focusId)
        return p ? [p.coords.lat, p.coords.lng] : [hotel.coords.lat, hotel.coords.lng]
      })()
    : [hotel.coords.lat, hotel.coords.lng]

  return (
    <div className="page">
      <TripBar
        trip={trip}
        onBack={onBack}
        action={
          <div className="bar-actions">
            <button className="btn btn-ghost" onClick={() => setShowOverview(true)}>
              Itinerary so far
              {totalStops > 0 && <span className="pill">{totalStops}</span>}
            </button>
            <button className="btn btn-primary" onClick={onFinish} disabled={totalStops === 0}>
              Review
            </button>
          </div>
        }
      />

      <main className="page-body">
        <div className="page-head">
          <span className="eyebrow">Staying at {hotel.name} · {hotel.neighborhood}</span>
          <h1>Fill the days</h1>
          <span className="lede">
            Everything shown is within a half-hour walk of your room. Add what appeals — the order
            is simply the order you choose.
          </span>
        </div>

        <div className="day-tabs" role="tablist" aria-label="Days">
          {Array.from({ length: trip.days }, (_, i) => {
            const count = (trip.itinerary[i] ?? []).length
            return (
              <button
                key={i}
                role="tab"
                aria-selected={i === day}
                className={`day-tab ${i === day ? 'is-active' : ''}`}
                onClick={() => { setDay(i); setFocusId(null) }}
              >
                <span className="day-tab-label">Day {i + 1}</span>
                <span className="day-tab-count">
                  {count === 0 ? 'empty' : `${count} ${count === 1 ? 'stop' : 'stops'}`}
                </span>
              </button>
            )
          })}
        </div>

        <div className="planner-layout">
          {/* ---------------- left: map + list ---------------- */}
          <div className="planner-main">
            <div className="filter-bar">
              <div className="segmented" role="group" aria-label="Filter places">
                {(['both', 'food', 'activity'] as Filter[]).map((f) => (
                  <button
                    key={f}
                    className={filter === f ? 'is-on' : ''}
                    aria-pressed={filter === f}
                    onClick={() => setFilter(f)}
                  >
                    <span className="seg-long">
                      {f === 'both' ? 'Everything' : f === 'food' ? 'Food & drink' : 'Things to do'}
                    </span>
                    <span className="seg-short">
                      {f === 'both' ? 'All' : f === 'food' ? 'Food' : 'To do'}
                    </span>
                    <span className="seg-count">{counts[f]}</span>
                  </button>
                ))}
              </div>
              <div className="legend">
                <span><i className="dot-hotel" /> Hotel</span>
                <span><i className="dot-food" /> Food</span>
                <span><i className="dot-activity" /> Activities</span>
              </div>
            </div>

            <div className="map-frame">
              <MapContainer center={[hotel.coords.lat, hotel.coords.lng]} zoom={13} scrollWheelZoom={false}>
                <TileLayer url={TILE_URL} attribution={TILE_ATTR} />
                <MapFocus center={focusCenter} zoom={focusId ? 15 : 13} />

                <Circle
                  center={[hotel.coords.lat, hotel.coords.lng]}
                  radius={VICINITY_METRES}
                  pathOptions={{
                    color: '#4E2038', weight: 1.5, fillColor: '#4E2038',
                    fillOpacity: 0.05, dashArray: '4 6',
                  }}
                />

                {route.length > 1 && (
                  <Polyline
                    positions={route}
                    pathOptions={{ color: '#8A6D3B', weight: 2.5, opacity: 0.95, dashArray: '5 5' }}
                  />
                )}

                <Marker position={[hotel.coords.lat, hotel.coords.lng]} icon={pinIcon('hotel', '⌂', true)} />

                {pinned.map((place) => (
                  <Marker
                    key={place.id}
                    position={[place.coords.lat, place.coords.lng]}
                    icon={pinIcon(place.kind, GLYPH[place.kind], isAdded(place.id))}
                    eventHandlers={{ click: () => setFocusId(place.id) }}
                  />
                ))}
              </MapContainer>
            </div>

            <div className="list-head">
              <span className="eyebrow">
                {filter === 'both' ? 'Near your hotel' : filter === 'food' ? 'Food & drink nearby' : 'Things to do nearby'}
              </span>
              <span className="step-inline-note">nearest first</span>
            </div>

            <div className="place-list">
              {visible.map(({ place, metres }) => {
                const elsewhere = scheduledOn(place.id)
                return (
                  <div
                    key={place.id}
                    className={[
                      'place-row',
                      isAdded(place.id) ? 'is-added' : '',
                      focusId === place.id ? 'is-focused' : '',
                    ].join(' ').trim()}
                    onClick={() => setFocusId(place.id)}
                  >
                    <span className={`place-glyph ${place.kind}`} aria-hidden>{GLYPH[place.kind]}</span>
                    <span className="place-text">
                      <span className="place-title">
                        {place.name}
                        {elsewhere !== null && <span className="mini-tag">on day {elsewhere + 1}</span>}
                      </span>
                      <span className="place-sub">
                        {place.category} · {place.priceLevel} · {walkMinutes(metres)} min walk · {duration(place.durationMins)}
                      </span>
                    </span>
                    <button
                      className={`place-add ${isAdded(place.id) ? 'is-added' : ''}`}
                      aria-label={
                        isAdded(place.id)
                          ? `Remove ${place.name} from day ${day + 1}`
                          : `Add ${place.name} to day ${day + 1}`
                      }
                      onClick={(e) => { e.stopPropagation(); toggleStop(place.id) }}
                    >
                      {isAdded(place.id) ? '✓' : '+'}
                    </button>
                  </div>
                )
              })}
              {visible.length === 0 && (
                <p className="day-empty">Nothing of that kind within walking distance.</p>
              )}
            </div>
          </div>

          {/* ---------------- right: the day being built ---------------- */}
          <aside className="day-panel">
            <div className="day-panel-head">
              <span className="eyebrow">Your plan</span>
              <h3>Day {day + 1}</h3>
              {schedule.length > 0 && (
                <span className="day-panel-sub">
                  {clock(schedule[0].startMins - schedule[0].walkMins)}–{clock(totals.endMins)} ·{' '}
                  {duration(totals.walkMins)} walking
                </span>
              )}
            </div>

            <div className="day-hotel">
              <span className="place-glyph" aria-hidden>⌂</span>
              <span>Start from {hotel.name}</span>
            </div>

            {schedule.length === 0 ? (
              <p className="day-empty">Nothing planned yet.<br />Add a stop from the list.</p>
            ) : (
              <ol className="stop-list">
                {schedule.map((s, i) => (
                  <li className="stop" key={s.place.id}>
                    <span className="stop-time">{clock(s.startMins)}</span>
                    <span className="stop-body">
                      <span className="stop-name">{s.place.name}</span>
                      <span className="stop-meta">
                        {s.place.category} · {duration(s.place.durationMins)}
                        {s.walkMins > 0 && <> · {s.walkMins} min walk {i === 0 ? 'from the hotel' : 'from the last stop'}</>}
                      </span>
                      <span className="stop-tools">
                        <button onClick={() => moveStop(i, -1)} disabled={i === 0} aria-label={`Move ${s.place.name} earlier`}>↑</button>
                        <button onClick={() => moveStop(i, 1)} disabled={i === schedule.length - 1} aria-label={`Move ${s.place.name} later`}>↓</button>
                        {trip.days > 1 && (
                          <select
                            value=""
                            aria-label={`Move ${s.place.name} to another day`}
                            onChange={(e) => e.target.value && moveToDay(s.place.id, Number(e.target.value))}
                          >
                            <option value="">Move to…</option>
                            {Array.from({ length: trip.days }, (_, d) => d)
                              .filter((d) => d !== day)
                              .map((d) => (
                                <option key={d} value={d}>Day {d + 1}</option>
                              ))}
                          </select>
                        )}
                        <button className="stop-remove" onClick={() => toggleStop(s.place.id)} aria-label={`Remove ${s.place.name}`}>
                          Remove
                        </button>
                      </span>
                    </span>
                  </li>
                ))}
              </ol>
            )}

            <div className="panel-foot">
              {schedule.length > 0 && (
                <button className="btn btn-ghost" onClick={clearDay}>Clear this day</button>
              )}
              {day < trip.days - 1 && (
                <button className="btn btn-secondary" onClick={() => { setDay(day + 1); setFocusId(null) }}>
                  On to day {day + 2} →
                </button>
              )}
              <button className="btn btn-primary" onClick={onFinish} disabled={totalStops === 0}>
                Review itinerary
              </button>
              <span className="step-inline-note" style={{ textAlign: 'center' }}>
                {stopLabel(totalStops)} across {dayLabel(trip.days)}
              </span>
            </div>
          </aside>
        </div>
      </main>

      {showOverview && (
        <Overview
          trip={trip}
          onClose={() => setShowOverview(false)}
          onJump={(d) => { setDay(d); setFocusId(null); setShowOverview(false) }}
        />
      )}
    </div>
  )
}

/** The running "itinerary so far", readable from any day. */
function Overview({
  trip,
  onClose,
  onJump,
}: {
  trip: Trip
  onClose: () => void
  onJump: (day: number) => void
}) {
  const hotel = hotelById(trip.hotelId ?? '') ?? HOTELS[0]
  const total = Object.values(trip.itinerary).flat().length

  return (
    <Dialog label="Your itinerary so far" className="modal" onClose={onClose}>
      <div className="modal-head">
        <div>
          <span className="eyebrow">So far</span>
          <h2>Your itinerary</h2>
        </div>
        <button className="drawer-close" onClick={onClose} aria-label="Close">×</button>
      </div>

      <div className="modal-body">
        <p className="step-inline-note" style={{ display: 'block', marginBottom: '1.25rem' }}>
          {stopLabel(total)} · staying at {hotel.name}
        </p>

        {Array.from({ length: trip.days }, (_, d) => {
          const stops = (trip.itinerary[d] ?? [])
            .map((id) => placeById(id))
            .filter((p): p is Place => !!p)
          const schedule = buildDay(hotel, stops)

          return (
            <section className="overview-day" key={d}>
              <div className="overview-day-head">
                <h3>Day {d + 1}</h3>
                <button className="btn btn-ghost" onClick={() => onJump(d)}>
                  {stops.length === 0 ? 'Plan this day' : 'Edit'}
                </button>
              </div>
              {schedule.length === 0 ? (
                <p className="day-empty" style={{ padding: '0.6rem 0', textAlign: 'left' }}>
                  Nothing planned.
                </p>
              ) : (
                <ol className="overview-stops">
                  {schedule.map((s) => (
                    <li key={s.place.id}>
                      <span className="stop-time">{clock(s.startMins)}</span>
                      <span>
                        <span className="stop-name">{s.place.name}</span>
                        <span className="stop-meta">{s.place.category} · {s.place.neighborhood}</span>
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          )
        })}
      </div>
    </Dialog>
  )
}
