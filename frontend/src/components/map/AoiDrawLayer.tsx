import { useState } from 'react'
import { CircleMarker, Polygon, Polyline, useMapEvents } from 'react-leaflet'
import type { LatLngTuple } from 'leaflet'
import { rectangleFromCorners } from '../../lib/format'
import { cssVar } from '../../lib/tokens'

export type DrawMode = 'polygon' | 'rectangle'

interface AoiDrawLayerProps {
  active: boolean
  points: LatLngTuple[]
  onAddPoint: (point: LatLngTuple) => void
  /** Rectangle mode: two clicks (opposite corners) replace all points. */
  mode?: DrawMode
  onReplacePoints?: (points: LatLngTuple[]) => void
}

/** Click-to-place-vertex AOI drawing (polygon), or two-corner rectangle. */
export function AoiDrawLayer({ active, points, onAddPoint, mode = 'polygon', onReplacePoints }: AoiDrawLayerProps) {
  const [corner, setCorner] = useState<LatLngTuple | null>(null)

  useMapEvents({
    click(e) {
      if (!active) return
      const p: LatLngTuple = [e.latlng.lat, e.latlng.lng]
      if (mode === 'rectangle' && onReplacePoints) {
        if (!corner) {
          setCorner(p)
          onReplacePoints([p])
        } else {
          onReplacePoints(rectangleFromCorners(corner as [number, number], p as [number, number]))
          setCorner(null)
        }
        return
      }
      onAddPoint(p)
    },
  })

  if (points.length === 0) return null
  const stroke = cssVar('--dm-aoi-stroke')
  const fill = cssVar('--dm-navy-900')

  return (
    <>
      {points.length >= 3 ? (
        <Polygon positions={points} pathOptions={{ color: stroke, fillColor: fill, fillOpacity: 0.35, weight: 2 }} />
      ) : (
        <Polyline positions={points} pathOptions={{ color: stroke, weight: 2, dashArray: '4 4' }} />
      )}
      {points.map((p, idx) => (
        <CircleMarker key={idx} center={p} radius={5} pathOptions={{ color: '#FFFFFF', fillColor: fill, fillOpacity: 1, weight: 2 }} />
      ))}
    </>
  )
}
