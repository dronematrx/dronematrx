import { Plane } from 'lucide-react'
import type { Drone } from '../../api/types'
import { StatusPill } from '../ui/StatusPill'

/** Spec 6.2: compact fixed reference card -- line-art icon, NAME, MODEL NO -- anchored on the darkest surface. */
export function DroneReportCard({ drone }: { drone: Drone }) {
  return (
    <div
      style={{
        background: 'var(--dm-navy-900)',
        borderRadius: 'var(--dm-radius-md)',
        padding: 'var(--dm-space-md)',
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--dm-space-md)',
      }}
    >
      <div
        style={{
          width: '3.25rem',
          height: '3.25rem',
          borderRadius: 'var(--dm-radius-circle)',
          background: 'var(--dm-surface-inset)',
          display: 'grid',
          placeItems: 'center',
          flexShrink: 0,
        }}
      >
        <Plane size={22} color="var(--dm-text-on-dark)" />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: '1.1rem', letterSpacing: '.05em', textTransform: 'uppercase' }}>{drone.name}</div>
        <div className="dm-mono" style={{ fontSize: '.8rem', opacity: 0.75 }}>
          MODEL NO: {drone.model}
        </div>
      </div>
      <StatusPill label={drone.status} />
    </div>
  )
}
