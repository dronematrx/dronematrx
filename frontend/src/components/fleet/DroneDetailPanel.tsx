import { useEffect, useState } from 'react'
import * as dronesApi from '../../api/drones'
import type { DroneDetail, DroneStatus } from '../../api/types'
import { useAuthStore } from '../../store/useAuthStore'
import { useDroneStore } from '../../store/useDroneStore'
import { DroneReportCard } from './DroneReportCard'
import { TelemetryPanel } from './TelemetryPanel'
import { StatusPill } from '../ui/StatusPill'

const STATUS_OPTIONS: DroneStatus[] = ['online', 'active', 'offline', 'warning', 'critical', 'maintenance']
type Tab = 'telemetry' | 'payloads' | 'maintenance'

export function DroneDetailPanel({ droneId, onDeactivated }: { droneId: string; onDeactivated: () => void }) {
  const [detail, setDetail] = useState<DroneDetail | null>(null)
  const [tab, setTab] = useState<Tab>('telemetry')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const liveDrone = useDroneStore((s) => s.drones.find((d) => d.id === droneId))
  const updateDroneStore = useDroneStore((s) => s.updateDrone)
  const deactivateDroneStore = useDroneStore((s) => s.deactivateDrone)
  const role = useAuthStore((s) => s.user?.role)
  const canWrite = role === 'ADMIN' || role === 'OPERATOR'

  const reload = () => {
    setLoading(true)
    dronesApi
      .getDrone(droneId)
      .then((d) => setDetail(d))
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load drone'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- standard fetch-on-mount/id-change
    reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [droneId])

  if (loading && !detail) return <div style={{ padding: 'var(--dm-space-md)', opacity: 0.7 }}>Loading drone…</div>
  if (error) return <div style={{ padding: 'var(--dm-space-md)', color: 'var(--dm-status-alert)' }}>{error}</div>
  if (!detail) return null

  const drone = liveDrone ?? detail

  const handleStatusChange = async (status: DroneStatus) => {
    await updateDroneStore(droneId, { status })
    reload()
  }

  const handleDeactivate = async () => {
    if (!window.confirm(`Deactivate ${drone.name}? It will be marked offline and hidden from the active fleet list.`)) return
    await deactivateDroneStore(droneId)
    onDeactivated()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--dm-space-md)' }}>
      <DroneReportCard drone={drone} />

      <div style={{ display: 'flex', gap: '.5rem', fontSize: '.8rem' }}>
        <label style={{ opacity: 0.7, alignSelf: 'center' }}>Status:</label>
        <select
          value={drone.status}
          disabled={!canWrite}
          onChange={(e) => handleStatusChange(e.target.value as DroneStatus)}
          style={{ background: 'var(--dm-surface-inset)', color: 'var(--dm-text-on-dark)', border: 0, borderRadius: 'var(--dm-radius-sm)', padding: '.4rem .6rem' }}
        >
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {canWrite && (
          <button
            type="button"
            onClick={handleDeactivate}
            style={{ marginLeft: 'auto', background: 'var(--dm-status-alert)', color: 'white', border: 0, borderRadius: 'var(--dm-radius-sm)', padding: '.4rem .8rem', textTransform: 'uppercase', fontSize: '.75rem' }}
          >
            Deactivate
          </button>
        )}
      </div>

      <nav style={{ display: 'flex', gap: '.25rem', borderBottom: '1px solid var(--dm-slate-700)' }}>
        {(['telemetry', 'payloads', 'maintenance'] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            style={{
              background: 'transparent',
              border: 0,
              borderBottom: tab === t ? '2px solid var(--dm-text-on-dark)' : '2px solid transparent',
              color: 'var(--dm-text-on-dark)',
              opacity: tab === t ? 1 : 0.6,
              padding: '.6rem .9rem',
              textTransform: 'uppercase',
              fontSize: '.75rem',
              letterSpacing: '.05em',
            }}
          >
            {t}
          </button>
        ))}
      </nav>

      {tab === 'telemetry' && (
        <TelemetryPanel
          cards={[
            {
              title: 'Position',
              rows: [
                { label: 'Latitude', value: drone.latitude?.toFixed(5) ?? '—' },
                { label: 'Longitude', value: drone.longitude?.toFixed(5) ?? '—' },
                { label: 'Altitude', value: `${drone.altitude.toFixed(1)} m` },
                { label: 'Heading', value: `${drone.heading.toFixed(0)}°` },
              ],
            },
            {
              title: 'Flight State',
              rows: [
                { label: 'Speed', value: `${drone.speed.toFixed(1)} m/s` },
                { label: 'Flight Mode', value: drone.flight_mode },
                { label: 'Armed', value: drone.armed ? 'Yes' : 'No' },
                { label: 'GPS', value: drone.gps_status },
              ],
            },
            {
              title: 'Power & Link',
              rows: [
                { label: 'Battery', value: `${drone.battery.toFixed(0)}%` },
                { label: 'Link Quality', value: `${drone.link_quality.toFixed(0)}%` },
                { label: 'Flight Hours', value: drone.total_flight_hours.toFixed(1) },
                { label: 'Battery Cycles', value: String(drone.battery_cycles) },
              ],
            },
          ]}
        />
      )}

      {tab === 'payloads' && <PayloadsTab detail={detail} canWrite={canWrite} onChanged={reload} />}
      {tab === 'maintenance' && <MaintenanceTab detail={detail} canWrite={canWrite} onChanged={reload} />}
    </div>
  )
}

function PayloadsTab({ detail, canWrite, onChanged }: { detail: DroneDetail; canWrite: boolean; onChanged: () => void }) {
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [type, setType] = useState('camera')

  const submit = async () => {
    if (!name.trim()) return
    await dronesApi.addPayload(detail.id, { type, name, status: 'uncalibrated' })
    setName('')
    setAdding(false)
    onChanged()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '.5rem' }}>
      {detail.payloads.length === 0 && <p style={{ opacity: 0.6, fontSize: '.85rem' }}>No payloads registered.</p>}
      {detail.payloads.map((p) => (
        <div key={p.id} style={{ background: 'var(--dm-slate-700)', borderRadius: 'var(--dm-radius-md)', padding: 'var(--dm-space-sm) var(--dm-space-md)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ fontWeight: 600 }}>{p.name}</div>
            <div style={{ fontSize: '.75rem', opacity: 0.7, textTransform: 'uppercase' }}>{p.type}</div>
          </div>
          <StatusPill label={p.status} />
        </div>
      ))}
      {canWrite && !adding && (
        <button type="button" onClick={() => setAdding(true)} style={addButtonStyle}>
          + Add Payload
        </button>
      )}
      {canWrite && adding && (
        <div style={{ display: 'flex', gap: '.5rem' }}>
          <select value={type} onChange={(e) => setType(e.target.value)} style={inputStyle}>
            {['camera', 'thermal', 'multispectral', 'lidar', 'sprayer'].map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <input placeholder="Payload name" value={name} onChange={(e) => setName(e.target.value)} style={{ ...inputStyle, flex: 1 }} />
          <button type="button" onClick={submit} style={addButtonStyle}>
            Save
          </button>
        </div>
      )}
    </div>
  )
}

function MaintenanceTab({ detail, canWrite, onChanged }: { detail: DroneDetail; canWrite: boolean; onChanged: () => void }) {
  const [adding, setAdding] = useState(false)
  const [notes, setNotes] = useState('')
  const [technician, setTechnician] = useState('')
  const [category, setCategory] = useState('routine')

  const submit = async () => {
    if (!notes.trim() || !technician.trim()) return
    await dronesApi.addMaintenanceRecord(detail.id, {
      service_date: new Date().toISOString(),
      category: category as never,
      technician,
      notes,
      flight_hours_at_service: detail.total_flight_hours,
      battery_cycles_at_service: detail.battery_cycles,
    })
    setNotes('')
    setTechnician('')
    setAdding(false)
    onChanged()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '.5rem' }}>
      {detail.maintenance_records.length === 0 && <p style={{ opacity: 0.6, fontSize: '.85rem' }}>No maintenance records.</p>}
      {detail.maintenance_records.map((m) => (
        <div key={m.id} style={{ background: 'var(--dm-slate-700)', borderRadius: 'var(--dm-radius-md)', padding: 'var(--dm-space-sm) var(--dm-space-md)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ fontWeight: 600, textTransform: 'uppercase', fontSize: '.75rem' }}>{m.category}</span>
            <span className="dm-mono" style={{ fontSize: '.75rem', opacity: 0.7 }}>
              {new Date(m.service_date).toLocaleDateString()}
            </span>
          </div>
          <p style={{ fontSize: '.85rem', margin: '.3rem 0' }}>{m.notes}</p>
          <span style={{ fontSize: '.75rem', opacity: 0.6 }}>by {m.technician}</span>
        </div>
      ))}
      {canWrite && !adding && (
        <button type="button" onClick={() => setAdding(true)} style={addButtonStyle}>
          + Add Maintenance Record
        </button>
      )}
      {canWrite && adding && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '.5rem' }}>
          <select value={category} onChange={(e) => setCategory(e.target.value)} style={inputStyle}>
            {['routine', 'repair', 'upgrade', 'inspection'].map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <input placeholder="Technician" value={technician} onChange={(e) => setTechnician(e.target.value)} style={inputStyle} />
          <textarea placeholder="Notes" value={notes} onChange={(e) => setNotes(e.target.value)} style={{ ...inputStyle, minHeight: '4rem' }} />
          <button type="button" onClick={submit} style={addButtonStyle}>
            Save
          </button>
        </div>
      )}
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  border: 0,
  borderRadius: 'var(--dm-radius-sm)',
  padding: '.5rem .7rem',
  background: 'var(--dm-surface-inset)',
  color: 'var(--dm-text-on-dark)',
  fontSize: '.85rem',
}

const addButtonStyle: React.CSSProperties = {
  border: '1px dashed var(--dm-slate-500)',
  borderRadius: 'var(--dm-radius-sm)',
  padding: '.5rem',
  background: 'transparent',
  color: 'var(--dm-text-on-dark)',
  fontSize: '.8rem',
  textTransform: 'uppercase',
}
