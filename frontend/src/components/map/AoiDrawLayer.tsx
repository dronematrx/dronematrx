import { useMapEvents } from 'react-leaflet'
import { CircleMarker, Polygon, Polyline } from 'react-leaflet'
import type { LatLngTuple } from 'leaflet'

interface AoiDrawLayerProps {
  active: boolean
  points: LatLngTuple[]
  onAddPoint: (point: LatLngTuple) => void
}

/** Click-to-place-vertex AOI drawing, mirroring the existing Mission Control
 * boundary-drawing interaction, adapted from SVG canvas coordinates to real lat/lng. */
export function AoiDrawLayer({ active, points, onAddPoint }: AoiDrawLayerProps) {
  useMapEvents({
    click(e) {
      if (!active) return
      onAddPoint([e.latlng.lat, e.latlng.lng])
    },
  })

  if (points.length === 0) return null

  return (
    <>
      {points.length >= 3 ? (
        <Polygon positions={points} pathOptions={{ color: '#9C9FAB', fillColor: '#273046', fillOpacity: 0.35, weight: 2 }} />
      ) : (
        <Polyline positions={points} pathOptions={{ color: '#9C9FAB', weight: 2, dashArray: '4 4' }} />
      )}
      {points.map((p, idx) => (
        <CircleMarker key={idx} center={p} radius={5} pathOptions={{ color: '#FFFFFF', fillColor: '#273046', fillOpacity: 1, weight: 2 }} />
      ))}
    </>
  )
}
