import { Fragment } from 'react'
import { CircleMarker, Polyline, Tooltip } from 'react-leaflet'
import type { OrcaEvent, OrcaView } from '../../api/types'
import { arrow } from '../../lib/orcaGeometry'
import { cssVar } from '../../lib/tokens'

const VECTOR_SECONDS = 6 // arrows show where the drone would be in 6 s

interface OrcaLayerProps {
  view: OrcaView
  timeline: OrcaEvent[]
  showNeighbors?: boolean
}

/** Live ORCA visualisation on the shared map: position, velocity (white),
 * preferred velocity (grey dashed), ORCA avoidance (cyan), neighbour links,
 * predicted collisions (amber, pulsing, with TTC at the intersection point),
 * and recent conflict events. Everything comes from the backend engine. */
export function OrcaLayer({ view, timeline, showNeighbors = true }: OrcaLayerProps) {
  const white = cssVar('--dm-orca-velocity')
  const grey = cssVar('--dm-orca-preferred')
  const cyan = cssVar('--dm-orca-avoidance')
  const amber = cssVar('--dm-orca-alert')
  const byId = new Map(view.drones.map((d) => [d.drone_id, d]))
  const predicted = new Set(view.predictions.flatMap((p) => [p.a_id, p.b_id]))
  const recentEvents = timeline.filter((e) => e.kind === 'conflict_predicted' || e.kind === 'conflict_cleared').slice(-6)

  return (
    <>
      {showNeighbors &&
        view.drones.flatMap((d) =>
          d.neighbors
            .filter((n) => d.drone_id < n && byId.has(n))
            .map((n) => {
              const o = byId.get(n)!
              return (
                <Polyline key={`nb-${d.drone_id}-${n}`} positions={[[d.lat, d.lon], [o.lat, o.lon]]} pathOptions={{ color: grey, weight: 1, opacity: 0.6, dashArray: '2 6' }} />
              )
            }),
        )}

      {view.predictions.map((p) => {
        const a = byId.get(p.a_id)
        const b = byId.get(p.b_id)
        if (!a || !b) return null
        return [
          <Polyline key={`pa-${p.a_id}-${p.b_id}`} positions={[[a.lat, a.lon], p.point]} pathOptions={{ color: amber, weight: 3, dashArray: '8 6', className: 'dm-orca-predict' }} />,
          <Polyline key={`pb-${p.a_id}-${p.b_id}`} positions={[[b.lat, b.lon], p.point]} pathOptions={{ color: amber, weight: 3, dashArray: '8 6', className: 'dm-orca-predict' }} />,
          <CircleMarker key={`pp-${p.a_id}-${p.b_id}`} center={p.point} radius={9} pathOptions={{ color: amber, weight: 3, fillOpacity: 0.25 }}>
            <Tooltip permanent direction="top" className="dm-orca-ttc">
              ⚠ {p.a} ↔ {p.b} · TTC {p.ttc_s.toFixed(1)} s
            </Tooltip>
          </CircleMarker>,
        ]
      })}

      {recentEvents
        .filter((e) => e.kind === 'conflict_predicted' && Array.isArray(e.details.point_geo))
        .map((e) => {
          const point = e.details.point_geo as [number, number]
          const key = [...e.drones].sort().join('|')
          const cleared = timeline.some((c) => c.kind === 'conflict_cleared' && c.seq > e.seq && [...c.drones].sort().join('|') === key)
          const names = e.drones.map((id) => byId.get(id)?.name ?? id.slice(0, 6)).join(' ↔ ')
          return (
            <CircleMarker key={`ev-${e.seq}`} center={point} radius={5} pathOptions={{ color: cleared ? cssVar('--dm-link-wifi') : amber, weight: 2, fillOpacity: 0.9, className: 'dm-orca-event' }}>
              <Tooltip>
                {cleared ? 'Conflict cleared' : 'Conflict predicted'}: {names} · {new Date(e.wall_time).toLocaleTimeString()}
              </Tooltip>
            </CircleMarker>
          )
        })}

      {view.drones.map((d) => {
        const vel = arrow(d.lat, d.lon, d.velocity, VECTOR_SECONDS)
        const pref = arrow(d.lat, d.lon, d.preferred_velocity, VECTOR_SECONDS)
        const avoid = Math.hypot(...d.avoidance_vector) > 0.05 ? arrow(d.lat, d.lon, d.safe_velocity, VECTOR_SECONDS) : null
        const alert = predicted.has(d.drone_id)
        return (
          <Fragment key={d.drone_id}>
            {pref && pref.map((seg, i) => <Polyline key={`p${i}`} positions={seg} pathOptions={{ color: grey, weight: 2, dashArray: '4 4', className: 'dm-orca-preferred' }} />)}
            {vel && vel.map((seg, i) => <Polyline key={`v${i}`} positions={seg} pathOptions={{ color: white, weight: 2, className: 'dm-orca-velocity' }} />)}
            {avoid && avoid.map((seg, i) => <Polyline key={`a${i}`} positions={seg} pathOptions={{ color: cyan, weight: 3, className: 'dm-orca-avoidance' }} />)}
            <CircleMarker
              center={[d.lat, d.lon]}
              radius={6}
              pathOptions={{ color: alert ? amber : white, fillColor: d.band === 0 ? grey : cssVar('--dm-navy-900'), fillOpacity: 1, weight: alert ? 3 : 2, className: 'dm-orca-drone' }}
            >
              <Tooltip permanent direction="right" offset={[8, 0]} className="dm-orca-label">
                {d.name} B{d.band ?? '-'}{d.mode !== 'scenario' && d.mode !== 'patrol' ? ` ${d.mode.toUpperCase()}` : ''}
              </Tooltip>
            </CircleMarker>
          </Fragment>
        )
      })}
    </>
  )
}
