import { useEffect, type ReactNode } from 'react'
import type { LatLngBoundsExpression, LatLngTuple, Map as LeafletMap } from 'leaflet'
import { HQ_CENTER } from '../../lib/mapGeometry'
import { MapContainer, TileLayer, useMap } from 'react-leaflet'
import type { Drone } from '../../api/types'
import { DroneMarker } from './DroneMarker'
import { InstrumentRail } from './InstrumentRail'

/** Leaflet snapshots container size at init and won't notice later layout
 * settling (fonts, flex resolution) unless nudged. */
function MapResizeHandler() {
  const map = useMap()
  useEffect(() => {
    const invalidate = () => map.invalidateSize()
    const timer = window.setTimeout(invalidate, 150)
    window.addEventListener('resize', invalidate)
    return () => {
      window.clearTimeout(timer)
      window.removeEventListener('resize', invalidate)
    }
  }, [map])
  return null
}

/** Fit the view to `bounds` whenever the key changes (e.g. a new AOI loads). */
function FitBounds({ bounds, fitKey, maxZoom }: { bounds: LatLngBoundsExpression | null; fitKey: string | null; maxZoom: number }) {
  const map = useMap()
  useEffect(() => {
    if (bounds && fitKey) map.fitBounds(bounds, { padding: [24, 24], maxZoom })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refit only when the key changes
  }, [map, fitKey])
  return null
}

/** Dev/E2E hook: tests convert lat/lng to screen pixels through the live map. */
function ExposeMapForTests() {
  const map = useMap()
  useEffect(() => {
    if (!import.meta.env.DEV) return
    const w = window as unknown as { __dmMap?: LeafletMap }
    w.__dmMap = map
    return () => {
      if (w.__dmMap === map) delete w.__dmMap
    }
  }, [map])
  return null
}

interface MissionMapProps {
  children?: ReactNode
  drones?: Drone[]
  showDrones?: boolean
  onDroneSelect?: (drone: Drone) => void
  fitTo?: LatLngBoundsExpression | null
  fitKey?: string | null
  center?: LatLngTuple
  zoom?: number
  fitMaxZoom?: number
}

/** The single shared Leaflet map used by Flight Plan, the mission wizard and
 * the geofence library (spec 6.5 viewport + 6.6 instrument rail). */
export function MissionMap({ children, drones = [], showDrones = true, onDroneSelect, fitTo = null, fitKey = null, center = HQ_CENTER, zoom = 14, fitMaxZoom = 17 }: MissionMapProps) {
  return (
    <>
      {/* Hatch pattern for NFZ fills (referenced from CSS: .dm-nfz-fill). */}
      <svg width="0" height="0" style={{ position: 'absolute' }} aria-hidden="true">
        <defs>
          <pattern id="dm-nfz-hatch" patternUnits="userSpaceOnUse" width="10" height="10" patternTransform="rotate(45)">
            <rect width="10" height="10" fill="var(--dm-geo-nfz)" fillOpacity="0.12" />
            <line x1="0" y1="0" x2="0" y2="10" stroke="var(--dm-geo-nfz)" strokeWidth="4" strokeOpacity="0.55" />
          </pattern>
        </defs>
      </svg>
      <MapContainer center={center} zoom={zoom} style={{ height: '100%', width: '100%' }} zoomControl={false}>
        <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        {children}
        {showDrones && drones.map((d) => <DroneMarker key={d.id} drone={d} onSelect={onDroneSelect ?? (() => undefined)} />)}
        <InstrumentRail />
        <MapResizeHandler />
        <FitBounds bounds={fitTo} fitKey={fitKey} maxZoom={fitMaxZoom} />
        <ExposeMapForTests />
      </MapContainer>
    </>
  )
}
