import { HOTELS, hotelById, placeById, type Place } from '../fixtures/madrid'
import Plate from '../components/Plate'
import TripBar from '../components/TripBar'
import { Ornament } from '../components/ui'
import { buildDay, clock, dayTotals, duration } from '../lib/schedule'
import { budgetLabel, days as dayLabel, nights as nightLabel, stops as stopLabel } from '../lib/labels'
import type { Trip } from '../types'

/** Screen 5 — the closing read-through. No map; this is the document. */
export default function Summary({
  trip,
  onBack,
  onRestart,
}: {
  trip: Trip
  onBack: () => void
  onRestart: () => void
}) {
  const hotel = hotelById(trip.hotelId ?? '') ?? HOTELS[0]
  const nights = Math.max(1, trip.days - 1)

  const days = Array.from({ length: trip.days }, (_, i) =>
    (trip.itinerary[i] ?? []).map((id) => placeById(id)).filter((p): p is Place => !!p),
  )

  const totalStops = days.flat().length
  const totalWalk = days.reduce((sum, stops) => sum + dayTotals(hotel, buildDay(hotel, stops)).walkMins, 0)

  return (
    <div className="page">
      <TripBar
        trip={trip}
        onBack={onBack}
        action={
          <div className="bar-actions">
            <button className="btn btn-ghost" onClick={onBack}>Keep editing</button>
            <button className="btn btn-secondary" onClick={() => window.print()}>Print</button>
          </div>
        }
      />

      <main className="page-body summary-sheet">
        <div className="summary-hero">
          <span className="eyebrow">Your itinerary</span>
          <h1 style={{ marginTop: '0.8rem' }}>{dayLabel(trip.days)} in Madrid</h1>
          <span className="lede">
            {stopLabel(totalStops)} · {trip.groupSize === 1 ? 'travelling solo' : `${trip.groupSize} travellers`} ·{' '}
            {budgetLabel(trip.budget)}
          </span>
          <Ornament />
        </div>

        <section className="summary-hotel">
          <Plate name={hotel.name} tag={hotel.neighborhood} />
          <div className="summary-hotel-body">
            <span className="eyebrow">Your room · {nightLabel(nights)}</span>
            <h2>{hotel.name}</h2>
            <div className="hotel-meta" style={{ justifyContent: 'flex-start', gap: '1.25rem' }}>
              <span className="stars" aria-label={`${hotel.stars} stars`}>{'★'.repeat(hotel.stars)}</span>
              <span className="hotel-price">€{hotel.pricePerNight} / night</span>
              <span>≈ €{(hotel.pricePerNight * nights).toLocaleString()} in total</span>
            </div>
            <p style={{ color: 'var(--ink-soft)', fontSize: '0.88rem' }}>{hotel.blurb}</p>
          </div>
        </section>

        {days.map((stops, i) => {
          const schedule = buildDay(hotel, stops)
          const totals = dayTotals(hotel, schedule)

          return (
            <section className="summary-day" key={i}>
              <div className="summary-day-label">
                <h3>Day {i + 1}</h3>
                <span>{stops.length === 0 ? 'Unplanned' : stopLabel(stops.length)}</span>
                {schedule.length > 0 && (
                  <span className="summary-day-totals">
                    {clock(schedule[0].startMins - schedule[0].walkMins)}–{clock(totals.endMins)}
                    <br />
                    {duration(totals.walkMins)} on foot
                  </span>
                )}
              </div>

              {schedule.length === 0 ? (
                <p className="day-empty" style={{ textAlign: 'left', padding: '0.5rem 0' }}>
                  Left open — and that is often the best day of the trip.
                </p>
              ) : (
                <ol className="summary-stops">
                  {schedule.map((s, n) => (
                    <li className="summary-stop" key={s.place.id}>
                      <span className="summary-stop-time">{clock(s.startMins)}</span>
                      <span>
                        <span className="summary-stop-name">{s.place.name}</span>
                        <span className="summary-stop-meta">
                          {s.place.category} · {s.place.neighborhood} · {s.place.priceLevel} ·{' '}
                          {duration(s.place.durationMins)}
                          {s.walkMins > 0 && (
                            <> · {s.walkMins} min walk from {n === 0 ? 'the hotel' : 'the last stop'}</>
                          )}
                        </span>
                        <span className="summary-stop-blurb">{s.place.blurb}</span>
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          )
        })}

        <p className="summary-footnote">
          {duration(totalWalk)} of walking across {dayLabel(trip.days)} · times are a guide, not a schedule
        </p>

        <div className="summary-actions">
          <button className="btn btn-secondary" onClick={onBack}>Keep editing</button>
          <button className="btn btn-primary" onClick={onRestart}>Plan another trip</button>
        </div>
      </main>
    </div>
  )
}
