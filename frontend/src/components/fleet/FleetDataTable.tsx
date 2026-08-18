import { Plane } from 'lucide-react'
import type { Drone } from '../../api/types'
import { StatusPill } from '../ui/StatusPill'

interface FleetDataTableProps {
  drones: Drone[]
  selectedId: string | null
  onSelect: (drone: Drone) => void
}

function formatRelative(iso: string | null): string {
  if (!iso) return 'never'
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`
  return `${Math.round(seconds / 3600)}h ago`
}

/** Spec 5.5/6.3: header in slate-700, alternating body rows, first column visual, last column ownership/traceability. */
export function FleetDataTable({ drones, selectedId, onSelect }: FleetDataTableProps) {
  return (
    <div style={{ overflowX: 'auto', borderRadius: 'var(--dm-radius-md)' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.85rem' }}>
        <thead>
          <tr style={{ background: 'var(--dm-slate-700)', textTransform: 'uppercase', fontWeight: 700 }}>
            <th style={{ padding: 'var(--dm-space-sm) var(--dm-space-md)', textAlign: 'left' }} scope="col" aria-label="Vehicle type" />
            <th style={{ padding: 'var(--dm-space-sm) var(--dm-space-md)', textAlign: 'left' }} scope="col">Name</th>
            <th style={{ padding: 'var(--dm-space-sm) var(--dm-space-md)', textAlign: 'left' }} scope="col">Model</th>
            <th style={{ padding: 'var(--dm-space-sm) var(--dm-space-md)', textAlign: 'left' }} scope="col">Status</th>
            <th style={{ padding: 'var(--dm-space-sm) var(--dm-space-md)', textAlign: 'left' }} scope="col">Battery</th>
            <th style={{ padding: 'var(--dm-space-sm) var(--dm-space-md)', textAlign: 'left' }} scope="col">Flight Mode</th>
            <th style={{ padding: 'var(--dm-space-sm) var(--dm-space-md)', textAlign: 'left' }} scope="col">Last Update</th>
          </tr>
        </thead>
        <tbody>
          {drones.map((drone, idx) => (
            <tr
              key={drone.id}
              onClick={() => onSelect(drone)}
              style={{
                cursor: 'pointer',
                background: drone.id === selectedId ? 'var(--dm-navy-900)' : idx % 2 === 0 ? 'var(--dm-slate-600)' : 'color-mix(in srgb, var(--dm-slate-600) 80%, white 6%)',
                borderBottom: '1px solid rgb(0 0 0 / .15)',
              }}
            >
              <td style={{ padding: 'var(--dm-space-sm) var(--dm-space-md)' }}>
                <Plane size={16} />
              </td>
              <td style={{ padding: 'var(--dm-space-sm) var(--dm-space-md)', fontWeight: 600 }}>{drone.name}</td>
              <td style={{ padding: 'var(--dm-space-sm) var(--dm-space-md)' }}>{drone.model}</td>
              <td style={{ padding: 'var(--dm-space-sm) var(--dm-space-md)' }}>
                <StatusPill label={drone.status} />
              </td>
              <td className="dm-mono" style={{ padding: 'var(--dm-space-sm) var(--dm-space-md)' }}>
                {drone.battery.toFixed(0)}%
              </td>
              <td style={{ padding: 'var(--dm-space-sm) var(--dm-space-md)' }}>{drone.flight_mode}</td>
              <td className="dm-mono" style={{ padding: 'var(--dm-space-sm) var(--dm-space-md)', opacity: 0.8 }}>
                {formatRelative(drone.last_telemetry_at)}
              </td>
            </tr>
          ))}
          {drones.length === 0 && (
            <tr>
              <td colSpan={7} style={{ padding: 'var(--dm-space-lg)', textAlign: 'center', opacity: 0.6 }}>
                No drones match the current filters.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
