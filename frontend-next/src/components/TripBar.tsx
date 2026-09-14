import { budgetLabel, days as dayLabel, travellers } from '../lib/labels'
import type { Trip } from '../types'

/** Persistent header: wordmark, one-line trip recap, optional action slot. */
export default function TripBar({
  trip,
  onBack,
  action,
}: {
  trip: Trip
  onBack?: () => void
  action?: React.ReactNode
}) {
  return (
    <header className="page-masthead">
      <div style={{ display: 'flex', alignItems: 'center', gap: '1.5rem' }}>
        {onBack && (
          <button type="button" className="btn btn-ghost" onClick={onBack}>
            ← Back
          </button>
        )}
        <span className="wordmark" style={{ fontSize: '1.15rem' }}>Roam</span>
      </div>
      <span className="trip-line">
        Madrid · <strong>{dayLabel(trip.days)}</strong> · <strong>{travellers(trip.groupSize)}</strong>
        {trip.budget && <> · <strong>{budgetLabel(trip.budget)}</strong></>}
      </span>
      <div>{action}</div>
    </header>
  )
}
