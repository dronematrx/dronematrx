import type { LatLngTuple } from 'leaflet'
import { CircleMarker, Polyline, Tooltip } from 'react-leaflet'
import type { Bundle, PlanTask, SpatialConflict } from '../../api/types'
import { formatDuration } from '../../lib/format'
import { cssVar, laneColor } from '../../lib/tokens'

const toLatLng = (c: number[]): LatLngTuple => [c[1], c[0]]

interface CbbaLanesProps {
  bundles: Bundle[]
  tasks?: PlanTask[]
  selectedDroneId?: string | null
  onSelect?: (bundle: Bundle) => void
  dimmed?: boolean
}

/** One coloured lane per drone (deterministic palette by colour_index), its
 * waypoint sequence, and an always-on ETA label. Unassigned tasks are grey. */
export function CbbaLanes({ bundles, tasks = [], selectedDroneId = null, onSelect, dimmed = false }: CbbaLanesProps) {
  const unassigned = tasks.filter((t) => t.status === 'unassigned')
  const grey = cssVar('--dm-slate-400')
  return (
    <>
      {unassigned.map((t) =>
        t.geometry.type === 'Point' ? (
          <CircleMarker key={t.task_key} center={toLatLng(t.geometry.coordinates as number[])} radius={7} pathOptions={{ color: grey, dashArray: '3 3', weight: 2, fillOpacity: 0.2 }}>
            <Tooltip>
              {t.task_key} unassigned: {t.unassigned_reason}
            </Tooltip>
          </CircleMarker>
        ) : (
          <Polyline key={t.task_key} positions={(t.geometry.coordinates as number[][]).map(toLatLng)} pathOptions={{ color: grey, weight: 3, dashArray: '4 6' }}>
            <Tooltip>
              {t.task_key} unassigned: {t.unassigned_reason}
            </Tooltip>
          </Polyline>
        ),
      )}
      {bundles.map((b) => {
        const color = laneColor(b.color_index)
        const selected = selectedDroneId !== null && b.drone_id === selectedDroneId
        const faded = dimmed || (selectedDroneId !== null && !selected)
        const positions = b.waypoints.map(toLatLng)
        return (
          <Polyline
            key={`${b.drone_id}-${b.color_index}`}
            positions={positions}
            pathOptions={{ color, weight: selected ? 5 : 3, opacity: faded ? 0.35 : 0.95, className: 'dm-cbba-lane' }}
            eventHandlers={onSelect ? { click: () => onSelect(b) } : undefined}
          >
            <Tooltip permanent={!faded} direction="center" className="dm-lane-label">
              {b.drone_name} · {b.task_count} tasks · ETA {formatDuration(b.eta_seconds)}
            </Tooltip>
          </Polyline>
        )
      })}
      {bundles.map((b) =>
        b.waypoints.slice(1, -1).map((wp, i) => (
          <CircleMarker
            key={`${b.drone_id}-wp-${i}`}
            center={toLatLng(wp)}
            radius={2.5}
            pathOptions={{ color: laneColor(b.color_index), weight: 1, fillOpacity: 1 }}
          />
        )),
      )}
      {bundles.map((b) =>
        b.waypoints.length ? (
          <CircleMarker
            key={`${b.drone_id}-home`}
            center={toLatLng(b.waypoints[0])}
            radius={6}
            pathOptions={{ color: '#FFFFFF', fillColor: laneColor(b.color_index), weight: 2, fillOpacity: 1 }}
          >
            <Tooltip>{b.drone_name} launch / return</Tooltip>
          </CircleMarker>
        ) : null,
      )}
    </>
  )
}

/** NFZ / geofence conflict geometry from the backend's PostGIS check, pulsing. */
export function ConflictOverlay({ conflicts }: { conflicts: SpatialConflict[] }) {
  const color = cssVar('--dm-geo-conflict')
  return (
    <>
      {conflicts.map((c, i) => {
        const g = c.intersection
        if (!g) return null
        const label = `${c.kind === 'nfz' ? 'NFZ CONFLICT' : c.kind.toUpperCase()} · ${c.geofence_name} (${c.scope})`
        const options = { color, weight: 4, dashArray: '6 4', className: 'dm-nfz-path', fillOpacity: 0.35, fillColor: color }
        if (g.type === 'Polygon') {
          const rings = g.coordinates as number[][][]
          return (
            <Polyline key={i} positions={rings[0].map(toLatLng)} pathOptions={options}>
              <Tooltip sticky>{label}</Tooltip>
            </Polyline>
          )
        }
        if (g.type === 'LineString') {
          return (
            <Polyline key={i} positions={(g.coordinates as number[][]).map(toLatLng)} pathOptions={options}>
              <Tooltip sticky>{label}</Tooltip>
            </Polyline>
          )
        }
        if (g.type === 'MultiLineString' || g.type === 'MultiPolygon') {
          const parts = g.type === 'MultiPolygon' ? (g.coordinates as number[][][][]).map((p) => p[0]) : (g.coordinates as number[][][])
          return parts.map((part, k) => (
            <Polyline key={`${i}-${k}`} positions={part.map(toLatLng)} pathOptions={options}>
              <Tooltip sticky>{label}</Tooltip>
            </Polyline>
          ))
        }
        if (g.type === 'Point') {
          return (
            <CircleMarker key={i} center={toLatLng(g.coordinates as number[])} radius={10} pathOptions={options}>
              <Tooltip>{label}</Tooltip>
            </CircleMarker>
          )
        }
        return null
      })}
    </>
  )
}
