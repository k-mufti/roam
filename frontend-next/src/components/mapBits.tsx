import { divIcon } from 'leaflet'
import { useEffect } from 'react'
import { useMap } from 'react-leaflet'

/** Map markers are plain HTML so they inherit the palette from styles.css. */
export function pinIcon(kind: 'hotel' | 'food' | 'activity', glyph: string, active = false) {
  const size = kind === 'hotel' ? 38 : 26
  return divIcon({
    className: '',
    html: `<div class="pin pin-${kind}${active ? ' is-active' : ''}">${glyph}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  })
}

/** Imperatively pans the map when the parent's focus changes. */
export function MapFocus({
  center,
  zoom,
}: {
  center: [number, number]
  zoom: number
}) {
  const map = useMap()
  useEffect(() => {
    map.flyTo(center, zoom, { duration: 0.8 })
  }, [map, center[0], center[1], zoom])
  return null
}

/* Plain OSM — the only genuinely key-free basemap. CARTO's Positron now
   stamps unkeyed tiles with a watermark, so the muting is done in CSS
   (see .leaflet-tile-pane in styles.css) rather than by the tile provider. */
export const TILE_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
export const TILE_ATTR =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
