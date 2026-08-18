import L from 'leaflet'
import { Marker, Popup } from 'react-leaflet'
import type { Drone } from '../../api/types'

const STATUS_COLOR: Record<string, string> = {
  online: '#4A5149',
  active: '#4A5149',
  offline: '#4B4F58',
  warning: '#818596',
  critical: '#7A3B3B',
  maintenance: '#818596',
}

function buildIcon(drone: Drone): L.DivIcon {
  const color = STATUS_COLOR[drone.status] ?? '#777C8F'
  const html = `
    <div style="transform: rotate(${drone.heading}deg); width: 28px; height: 28px; display:grid; place-items:center;">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 2 L19 20 L12 16 L5 20 Z" fill="${color}" stroke="#FFFFFF" stroke-width="1.2" />
      </svg>
    </div>`
  return L.divIcon({ html, className: 'dm-drone-icon', iconSize: [28, 28], iconAnchor: [14, 14] })
}

interface DroneMarkerProps {
  drone: Drone
  onSelect: (drone: Drone) => void
}

export function DroneMarker({ drone, onSelect }: DroneMarkerProps) {
  if (drone.latitude === null || drone.longitude === null) return null

  return (
    <Marker
      position={[drone.latitude, drone.longitude]}
      icon={buildIcon(drone)}
      eventHandlers={{ click: () => onSelect(drone) }}
    >
      <Popup>
        <strong>{drone.name}</strong>
        <br />
        {drone.model} &middot; {drone.status}
        <br />
        Battery: {drone.battery.toFixed(0)}% &middot; Alt: {drone.altitude.toFixed(0)}m
        <br />
        Mode: {drone.flight_mode}
      </Popup>
    </Marker>
  )
}
