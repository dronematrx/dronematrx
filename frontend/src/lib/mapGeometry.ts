import type { LatLngBoundsExpression, LatLngTuple } from 'leaflet'
import type { GeofenceType } from '../api/types'

export const HQ_CENTER: LatLngTuple = [23.2156, 72.6369]

export const GEOFENCE_LABEL: Record<GeofenceType, string> = {
  inclusion: 'Inclusion',
  exclusion: 'Exclusion',
  nfz: 'No-Fly Zone',
}

/** GeoJSON ring ([lng, lat]) -> Leaflet positions ([lat, lng]). */
export function ringToLatLng(ring: number[][]): LatLngTuple[] {
  return ring.map(([lng, lat]) => [lat, lng] as LatLngTuple)
}

export function polygonBounds(coords: number[][] | undefined | null): LatLngBoundsExpression | null {
  if (!coords || coords.length === 0) return null
  const lats = coords.map((c) => c[1])
  const lngs = coords.map((c) => c[0])
  return [
    [Math.min(...lats), Math.min(...lngs)],
    [Math.max(...lats), Math.max(...lngs)],
  ]
}
