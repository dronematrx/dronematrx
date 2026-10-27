import { useEffect, useMemo } from 'react'
import L from 'leaflet'
import { CircleMarker, Marker, Polyline, Rectangle, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import type { HeatCell } from '../../api/missionControl'
import type { LiveDrone } from './liveFleet'

const SRC_COLOR: Record<string, string> = { sitl: '#C9A15A', kinematic_demo: '#9AA0AE', hardware: '#5CC28A', none: '#6B6F7B' }
const CONN_OPACITY: Record<string, number> = { connected: 1, stale: 0.55, offline: 0.3, no_telemetry: 0.3 }

function icon(d: LiveDrone, selected: boolean): L.DivIcon {
  const color = SRC_COLOR[d.telemetry_source] ?? '#777C8F'
  const ring = selected ? '<circle cx="18" cy="18" r="16" fill="none" stroke="#FFD08A" stroke-width="3"/>' : ''
  const html = `
    <div data-testid="mc-marker-${d.id}" data-drone-id="${d.id}" data-selected="${selected}" data-lat="${d.latitude}" data-lon="${d.longitude}"
         style="width:36px;height:36px;opacity:${CONN_OPACITY[d.connection_state] ?? 1}">
      <svg width="36" height="36" viewBox="0 0 36 36" xmlns="http://www.w3.org/2000/svg">${ring}
        <g transform="rotate(${d.heading} 18 18)"><path d="M18 5 L26 28 L18 23 L10 28 Z" fill="${color}" stroke="#FFFFFF" stroke-width="1.4"/></g>
      </svg>
    </div>`
  return L.divIcon({ html, className: 'dm-drone-icon', iconSize: [36, 36], iconAnchor: [18, 18] })
}

function PanTo({ target }: { target: [number, number] | null }) {
  const map = useMap()
  const key = target ? target.join(',') : ''
  useEffect(() => {
    if (target) map.panTo(target, { animate: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- pan when the selection changes
  }, [key])
  return null
}

function ClickTarget({ active, onPick }: { active: boolean; onPick: (lat: number, lon: number) => void }) {
  useMapEvents({ click: (e) => active && onPick(e.latlng.lat, e.latlng.lng) })
  return null
}

interface Props {
  drones: LiveDrone[]
  selectedId: string | null
  onSelect: (id: string) => void
  centreOn: [number, number] | null
  showTracks: boolean
  tracks: Record<string, [number, number][]>
  accepted: { id: string; lat: number; lon: number; label: string; confidence: number }[]
  showDetections: boolean
  heat: HeatCell[] | null
  heatMax: number
  goto: { active: boolean; target: [number, number] | null; onPick: (lat: number, lon: number) => void }
}

export function McMapLayers({ drones, selectedId, onSelect, centreOn, showTracks, tracks, accepted, showDetections, heat, heatMax, goto }: Props) {
  const positioned = useMemo(() => drones.filter((d) => d.latitude != null && d.longitude != null), [drones])
  return (
    <>
      <PanTo target={centreOn} />
      <ClickTarget active={goto.active} onPick={goto.onPick} />
      {heat?.map((c, i) => (
        <Rectangle key={i} bounds={[[c.south, c.west], [c.north, c.east]]}
          pathOptions={{ stroke: false, fillColor: '#FF5A36', fillOpacity: 0.15 + 0.55 * (c.count / Math.max(1, heatMax)) }} className="dm-heat-cell">
          <Tooltip>{c.count} accepted detection{c.count === 1 ? '' : 's'}</Tooltip>
        </Rectangle>
      ))}
      {showTracks && Object.entries(tracks).map(([id, pts]) => pts.length > 1 && (
        <Polyline key={id} positions={pts} pathOptions={{ color: id === selectedId ? '#FFD08A' : '#7FB3FF', weight: id === selectedId ? 3 : 2, opacity: 0.8 }} className="dm-mc-track" />
      ))}
      {showDetections && accepted.map((a) => (
        <CircleMarker key={a.id} center={[a.lat, a.lon]} radius={7} pathOptions={{ color: '#FFFFFF', weight: 2, fillColor: '#5CC28A', fillOpacity: 0.95 }}
          className={`dm-det-marker dm-det-marker-${a.id}`}>
          <Tooltip>{a.label} · {(a.confidence * 100).toFixed(0)}% · accepted</Tooltip>
        </CircleMarker>
      ))}
      {goto.target && (
        <CircleMarker center={goto.target} radius={8} pathOptions={{ color: '#FFD08A', weight: 3, fillOpacity: 0 }}>
          <Tooltip permanent direction="top">GO TO</Tooltip>
        </CircleMarker>
      )}
      {positioned.map((d) => (
        <Marker key={d.id} position={[d.latitude as number, d.longitude as number]} icon={icon(d, d.id === selectedId)}
          eventHandlers={{ click: () => onSelect(d.id) }} zIndexOffset={d.id === selectedId ? 1000 : 0}>
          <Tooltip direction="top" offset={[0, -14]} opacity={0.95}>
            <strong>{d.name}</strong> · {d.telemetry_source === 'sitl' ? 'SITL' : d.telemetry_source === 'kinematic_demo' ? 'KINEMATIC DEMO' : d.telemetry_source.toUpperCase()}
            <br />{d.flight_mode} · {d.battery.toFixed(0)}% · {d.altitude.toFixed(1)} m · {d.connection_state}
          </Tooltip>
        </Marker>
      ))}
    </>
  )
}
