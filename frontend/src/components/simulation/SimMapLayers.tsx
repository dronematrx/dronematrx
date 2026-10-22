import { Fragment, type CSSProperties } from 'react'
import L from 'leaflet'
import type { LatLngTuple } from 'leaflet'
import { CircleMarker, Marker, Polyline, Rectangle, Tooltip, useMapEvents } from 'react-leaflet'
import type { Drone, PlanTask } from '../../api/types'
import type { SimInstance, SimSnapshot, TrackPoint } from '../../api/simulations'
import { cssVar, laneColor } from '../../lib/tokens'

const ll = (c: number[]): LatLngTuple => [c[1], c[0]]
export const STALE_MS = 3000

function taskLatLngs(t: PlanTask): LatLngTuple[] {
  return t.geometry.type === 'Point' ? [ll(t.geometry.coordinates as number[])] : (t.geometry.coordinates as number[][]).map(ll)
}

function markerIcon(drone: Drone, color: string, state: string, stale: boolean, label: string): L.DivIcon {
  const failed = state === 'failed'
  const fill = failed ? '#D03A2F' : color
  const html = `
    <div data-testid="sim-marker-${drone.uin}" data-drone-id="${drone.id}" data-lat="${drone.latitude}" data-lon="${drone.longitude}" data-state="${state}" data-stale="${stale}"
         style="position:relative;width:30px;height:30px;opacity:${stale ? 0.45 : 1}">
      <div style="transform: rotate(${drone.heading}deg); width:30px; height:30px; display:grid; place-items:center;">
        <svg width="24" height="24" viewBox="0 0 24 24"><path d="M12 2 L19 20 L12 16 L5 20 Z" fill="${fill}" stroke="#FFFFFF" stroke-width="1.4"/></svg>
      </div>
      <span style="position:absolute;left:26px;top:-4px;white-space:nowrap;font:600 10px 'Space Mono',monospace;color:#fff;background:${failed ? '#7A3B3B' : 'rgba(39,48,70,.9)'};padding:1px 4px;border-radius:3px">
        ${label}${failed ? ' ✕' : stale ? ' STALE' : ''}</span>
    </div>`
  return L.divIcon({ html, className: 'dm-sim-drone-icon', iconSize: [30, 30], iconAnchor: [15, 15] })
}

interface SimMapLayersProps {
  instances: SimInstance[]
  live: SimSnapshot | null
  tracks: Record<string, TrackPoint[]>
  tasks: PlanTask[]
  drones: Drone[]
  now: number
  pickActive?: boolean
  onPick?: (lat: number, lon: number) => void
}

function PickLayer({ active, onPick }: { active: boolean; onPick?: (lat: number, lon: number) => void }) {
  useMapEvents({
    click(e) {
      if (active && onPick) onPick(e.latlng.lat, e.latlng.lng)
    },
  })
  return null
}

/** Planned routes, actual (telemetry) tracks, task progress, live SITL drone
 * markers and obstacles over the mission AOI. */
export function SimMapLayers({ instances, live, tracks, tasks, drones, now, pickActive = false, onPick }: SimMapLayersProps) {
  const status = live?.progress.task_status ?? {}
  const byUin = new Map((live?.drones ?? []).map((d) => [d.uin, d]))
  const done = cssVar('--dm-lane-2')
  const failedColor = cssVar('--dm-geo-nfz')
  return (
    <>
      {/* Task progress layer */}
      {tasks.map((t) => {
        const st = status[t.task_key]?.status
        if (st === 'completed') {
          return t.geometry.type === 'Point' ? (
            <CircleMarker key={`c-${t.task_key}`} center={taskLatLngs(t)[0]} radius={7} pathOptions={{ color: done, fillColor: done, fillOpacity: 0.8, weight: 2 }} />
          ) : (
            <Polyline key={`c-${t.task_key}`} positions={taskLatLngs(t)} pathOptions={{ color: done, weight: 6, opacity: 0.55 }} className="dm-sim-completed" />
          )
        }
        if (st === 'failed') {
          return (
            <Polyline key={`f-${t.task_key}`} positions={taskLatLngs(t).length > 1 ? taskLatLngs(t) : [taskLatLngs(t)[0], taskLatLngs(t)[0]]} pathOptions={{ color: failedColor, weight: 4, dashArray: '2 6' }}>
              <Tooltip>{t.task_key} failed / interrupted</Tooltip>
            </Polyline>
          )
        }
        return null
      })}
      {instances.map((inst) => {
        const color = laneColor(inst.instance_index)
        const liveDrone = byUin.get(inst.uin)
        const planned = (liveDrone?.planned_route?.length ? liveDrone.planned_route : inst.planned_route) ?? []
        const track = tracks[inst.uin] ?? []
        const current = liveDrone?.current_task ? tasks.find((t) => t.task_key === liveDrone.current_task) : undefined
        const drone = drones.find((d) => d.id === inst.drone_id)
        return (
          <Fragment key={inst.uin}>
            {planned.length > 1 && <Polyline positions={planned.map(ll)} pathOptions={{ color, weight: 2, opacity: 0.6, dashArray: '6 6' }} className="dm-sim-planned" />}
            {track.length > 1 && <Polyline positions={track.map((p) => [p[1], p[2]] as LatLngTuple)} pathOptions={{ color, weight: 3, opacity: 0.95 }} className="dm-sim-track" />}
            {current && drone?.latitude != null && drone.longitude != null && (
              <Polyline positions={[[drone.latitude, drone.longitude] as LatLngTuple, ...taskLatLngs(current)]} pathOptions={{ color: '#FFFFFF', weight: 4, opacity: 0.9 }} className="dm-sim-current" />
            )}
            <CircleMarker center={[inst.spawn.latitude, inst.spawn.longitude]} radius={4} pathOptions={{ color: '#FFFFFF', fillColor: color, fillOpacity: 1, weight: 1 }}>
              <Tooltip>{inst.name} launch pad</Tooltip>
            </CircleMarker>
            {drone && drone.latitude != null && drone.longitude != null && (
              <Marker
                position={[drone.latitude, drone.longitude]}
                icon={markerIcon(drone, color, liveDrone?.state ?? inst.state, drone.last_telemetry_at ? now - Date.parse(drone.last_telemetry_at) > STALE_MS : true, inst.name)}
              >
                <Tooltip direction="top" offset={[0, -12]}>
                  <div style={{ fontSize: '.75rem' }}>
                    <strong>{inst.name}</strong> · SIMULATION
                    <br />
                    {liveDrone?.state ?? inst.state} · {drone.flight_mode} · hdg {drone.heading.toFixed(0)}°
                    <br />
                    Battery {drone.battery.toFixed(0)}% · alt {drone.altitude.toFixed(1)} m · {drone.speed.toFixed(1)} m/s
                    <br />
                    Task {liveDrone?.current_task ?? '—'} · link {drone.link_quality.toFixed(0)}%
                    <br />
                    {drone.latitude.toFixed(6)}, {drone.longitude.toFixed(6)}
                  </div>
                </Tooltip>
              </Marker>
            )}
          </Fragment>
        )
      })}
      {(live?.obstacles ?? []).map((o) => {
        const dLat = o.size_m[0] / 2 / 111_320
        const dLon = o.size_m[1] / 2 / (111_320 * Math.cos((o.latitude * Math.PI) / 180))
        return (
          <Rectangle
            key={o.name}
            bounds={[
              [o.latitude - dLat, o.longitude - dLon],
              [o.latitude + dLat, o.longitude + dLon],
            ]}
            pathOptions={{ color: failedColor, fillColor: failedColor, fillOpacity: 0.6, weight: 2 }}
          >
            <Tooltip>{o.name} (Gazebo obstacle)</Tooltip>
          </Rectangle>
        )
      })}
      <PickLayer active={pickActive} onPick={onPick} />
    </>
  )
}

export function SimLegend() {
  const item = (label: string, style: CSSProperties) => (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <span style={{ width: 18, height: 0, borderTop: '3px solid', ...style }} />
      {label}
    </span>
  )
  return (
    <div className="dm-mono" style={{ display: 'flex', gap: '.8rem', flexWrap: 'wrap', fontSize: '.68rem' }} data-testid="sim-legend">
      {item('PLANNED', { borderTopStyle: 'dashed', borderColor: 'var(--dm-lane-1)' })}
      {item('ACTUAL (TELEMETRY)', { borderColor: 'var(--dm-lane-1)' })}
      {item('COMPLETED', { borderColor: 'var(--dm-lane-2)', borderTopWidth: 5 })}
      {item('CURRENT SEGMENT', { borderColor: '#FFFFFF' })}
      {item('FAILED / INTERRUPTED', { borderTopStyle: 'dotted', borderColor: 'var(--dm-geo-nfz)' })}
    </div>
  )
}
