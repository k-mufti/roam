import { PACKAGES, hotelById, type Package } from '../fixtures/madrid'
import Plate from '../components/Plate'
import TripBar from '../components/TripBar'
import { days as dayLabel } from '../lib/labels'
import type { Trip } from '../types'

/**
 * Screen 2a — the curated gallery. Picking one hands the whole package
 * up to App, which pre-loads its hotel and spreads its places across days.
 */
export default function Packages({
  trip,
  onPick,
  onBack,
}: {
  trip: Trip
  onPick: (pkg: Package) => void
  onBack: () => void
}) {
  return (
    <div className="page">
      <TripBar trip={trip} onBack={onBack} />
      <main className="page-body">
        <div className="page-head">
          <span className="eyebrow">Curated</span>
          <h1>Four trips we have already walked</h1>
          <span className="lede">
            Each one is a complete itinerary — hotel, tables, mornings. Take it as it is, or change
            anything once it is loaded.
          </span>
        </div>

        <div className="package-grid">
          {PACKAGES.map((pkg) => {
            const hotel = hotelById(pkg.hotelId)
            return (
              <article
                key={pkg.id}
                className="package-card"
                role="button"
                tabIndex={0}
                onClick={() => onPick(pkg)}
                onKeyDown={(e) => e.key === 'Enter' && onPick(pkg)}
              >
                <Plate name={pkg.name} tag={hotel?.neighborhood} />
                <div className="package-body">
                  <div className="tag-row">
                    <span className="tag plum">{dayLabel(pkg.days)}</span>
                    <span className="tag">{pkg.budget} budget</span>
                    <span className="tag">{pkg.placeIds.length} stops</span>
                  </div>
                  <h3 className="package-name">{pkg.name}</h3>
                  <p className="package-desc">{pkg.description}</p>
                  <p className="package-price">
                    {pkg.priceRange}
                    {hotel && <> · staying at {hotel.name}</>}
                  </p>
                </div>
              </article>
            )
          })}
        </div>

        <p className="step-inline-note" style={{ marginTop: '2rem', display: 'block' }}>
          You asked for {dayLabel(trip.days)} — a package of a different length is spread across
          the days you have, and you can move anything afterwards.
        </p>
      </main>
    </div>
  )
}
