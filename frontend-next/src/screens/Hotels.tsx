import { useEffect, useRef, useState } from 'react'
import { Circle, MapContainer, Marker, TileLayer } from 'react-leaflet'
import { CITY, HOTELS, type Hotel } from '../fixtures/madrid'
import Plate from '../components/Plate'
import TripBar from '../components/TripBar'
import Dialog from '../components/Dialog'
import { MapFocus, TILE_ATTR, TILE_URL, pinIcon } from '../components/mapBits'
import { VICINITY_METRES } from '../lib/constants'
import { days as dayLabel } from '../lib/labels'
import type { Trip } from '../types'

/**
 * Screen 3 — map above, a rail of hotel cards below.
 * Hovering/clicking a card highlights its pin and vice versa; opening a card
 * shows the detail drawer, from which the hotel is actually chosen.
 */
export default function Hotels({
  trip,
  onSelect,
  onBack,
}: {
  trip: Trip
  onSelect: (hotelId: string) => void
  onBack: () => void
}) {
  const [activeId, setActiveId] = useState<string | null>(HOTELS[0].id)
  const [openId, setOpenId] = useState<string | null>(null)
  const [confirmedId, setConfirmedId] = useState<string | null>(null)
  const railRef = useRef<HTMLDivElement>(null)

  const open = HOTELS.find((h) => h.id === openId) ?? null
  const confirmed = HOTELS.find((h) => h.id === confirmedId) ?? null

  /* Keep the rail in sync when a pin is clicked. */
  useEffect(() => {
    if (!activeId || !railRef.current) return
    const card = railRef.current.querySelector<HTMLElement>(`[data-hotel="${activeId}"]`)
    card?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' })
  }, [activeId])

  /* Once confirmed, let the map settle on the hotel before moving on. */
  useEffect(() => {
    if (!confirmedId) return
    const t = setTimeout(() => onSelect(confirmedId), 1250)
    return () => clearTimeout(t)
  }, [confirmedId, onSelect])

  const focus: [number, number] = confirmed
    ? [confirmed.coords.lat, confirmed.coords.lng]
    : [CITY.center.lat, CITY.center.lng]

  return (
    <div className="page">
      <TripBar trip={trip} onBack={onBack} />
      <main className="page-body">
        <div className="page-head">
          <span className="eyebrow">Step one of the plan</span>
          <h1>Where would you like to stay?</h1>
          <span className="lede">
            Four rooms we would happily book ourselves. The neighbourhood decides more of the trip
            than the hotel does.
          </span>
        </div>

        <div className="map-frame">
          <MapContainer
            center={[CITY.center.lat, CITY.center.lng]}
            zoom={14}
            scrollWheelZoom={false}
            zoomControl
          >
            <TileLayer url={TILE_URL} attribution={TILE_ATTR} />
            <MapFocus center={focus} zoom={confirmed ? 15 : 14} />

            {confirmed && (
              <Circle
                center={[confirmed.coords.lat, confirmed.coords.lng]}
                radius={VICINITY_METRES}
                pathOptions={{
                  color: '#4E2038',
                  weight: 1.5,
                  fillColor: '#4E2038',
                  fillOpacity: 0.07,
                  dashArray: '4 6',
                }}
              />
            )}

            {HOTELS.map((h) => (
              <Marker
                key={h.id}
                position={[h.coords.lat, h.coords.lng]}
                icon={pinIcon('hotel', '⌂', h.id === activeId || h.id === confirmedId)}
                opacity={confirmedId && confirmedId !== h.id ? 0.35 : 1}
                eventHandlers={{
                  click: () => { setActiveId(h.id); setOpenId(h.id) },
                }}
              />
            ))}
          </MapContainer>
        </div>

        {confirmed ? (
          <p className="lede" style={{ display: 'block', marginTop: '1.75rem', textAlign: 'center' }}>
            {confirmed.name} it is — planning the {dayLabel(trip.days)} around {confirmed.neighborhood}…
          </p>
        ) : (
          <div className="rail" ref={railRef}>
            {HOTELS.map((h) => (
              <article
                key={h.id}
                data-hotel={h.id}
                className={`hotel-card ${h.id === activeId ? 'is-active' : ''}`}
                role="button"
                tabIndex={0}
                onMouseEnter={() => setActiveId(h.id)}
                onClick={() => { setActiveId(h.id); setOpenId(h.id) }}
                onKeyDown={(e) => e.key === 'Enter' && setOpenId(h.id)}
              >
                <Plate name={h.name} tag={h.neighborhood} />
                <div className="hotel-body">
                  <h3 className="hotel-name">{h.name}</h3>
                  <div className="hotel-meta">
                    <span className="stars">{'★'.repeat(h.stars)}</span>
                    <span className="hotel-price">€{h.pricePerNight}<span style={{ fontSize: '0.7rem', fontStyle: 'normal' }}> / night</span></span>
                  </div>
                  <p className="hotel-blurb">{h.blurb}</p>
                </div>
              </article>
            ))}
          </div>
        )}
      </main>

      {open && !confirmed && (
        <HotelDrawer
          hotel={open}
          nights={Math.max(1, trip.days - 1)}
          onClose={() => setOpenId(null)}
          onSelect={() => { setOpenId(null); setConfirmedId(open.id) }}
        />
      )}
    </div>
  )
}

function HotelDrawer({
  hotel,
  nights,
  onClose,
  onSelect,
}: {
  hotel: Hotel
  nights: number
  onClose: () => void
  onSelect: () => void
}) {
  return (
    <Dialog label={hotel.name} onClose={onClose}>
      <button className="drawer-close" onClick={onClose} aria-label="Close">×</button>
      <Plate name={hotel.name} tag={hotel.neighborhood} />
      <div className="drawer-body">
        <div>
          <span className="eyebrow">{hotel.neighborhood}</span>
          <h2 style={{ marginTop: '0.5rem' }}>{hotel.name}</h2>
        </div>
        <div className="hotel-meta">
          <span className="stars" aria-label={`${hotel.stars} stars`}>{'★'.repeat(hotel.stars)}</span>
          <span className="hotel-price">€{hotel.pricePerNight} / night</span>
        </div>
        <hr className="rule" />
        <p style={{ color: 'var(--ink-soft)' }}>{hotel.blurb}</p>

        <div className="ornament"><span>◆</span></div>

        <span className="eyebrow">What guests say</span>
        {hotel.reviews.map((r) => (
          <div className="review" key={r.author}>
            <p>&ldquo;{r.text}&rdquo;</p>
            <cite>{r.author}</cite>
          </div>
        ))}

        <div className="drawer-actions">
          <button className="btn btn-secondary" onClick={onClose}>Keep looking</button>
          <button className="btn btn-primary" onClick={onSelect}>Select this hotel</button>
        </div>
        <p className="step-inline-note" style={{ textAlign: 'center' }}>
          About €{hotel.pricePerNight * nights} for a {nights}-night stay
        </p>
      </div>
    </Dialog>
  )
}
