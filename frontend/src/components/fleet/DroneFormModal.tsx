import { useState } from 'react'
import { useDroneStore } from '../../store/useDroneStore'

export function DroneFormModal({ onClose }: { onClose: () => void }) {
  const createDrone = useDroneStore((s) => s.createDrone)
  const [name, setName] = useState('')
  const [model, setModel] = useState('')
  const [vehicleType, setVehicleType] = useState('quadrotor')
  const [uin, setUin] = useState('')
  const [latitude, setLatitude] = useState('23.2156')
  const [longitude, setLongitude] = useState('72.6369')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const submit = async () => {
    if (!name.trim() || !model.trim()) {
      setError('Name and model are required.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await createDrone({
        name,
        model,
        vehicle_type: vehicleType,
        uin: uin || null,
        latitude: latitude ? Number(latitude) : null,
        longitude: longitude ? Number(longitude) : null,
      })
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create drone')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgb(0 0 0 / .55)', display: 'grid', placeItems: 'center', zIndex: 50 }}>
      <div style={{ background: 'var(--dm-navy-900)', color: 'var(--dm-text-on-dark)', borderRadius: 'var(--dm-radius-md)', padding: 'var(--dm-space-lg)', width: 'min(28rem, 92vw)' }}>
        <h2 style={{ margin: '0 0 1rem', textTransform: 'uppercase', letterSpacing: '.06em' }}>Register Drone</h2>
        <div style={{ display: 'grid', gap: '.75rem' }}>
          <Field label="Name">
            <input value={name} onChange={(e) => setName(e.target.value)} style={inputStyle} placeholder="DMX-009" />
          </Field>
          <Field label="Model">
            <input value={model} onChange={(e) => setModel(e.target.value)} style={inputStyle} placeholder="M30" />
          </Field>
          <Field label="Vehicle Type">
            <select value={vehicleType} onChange={(e) => setVehicleType(e.target.value)} style={inputStyle}>
              {['quadrotor', 'hexarotor', 'octorotor', 'fixed_wing'].map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </Field>
          <Field label="UIN (optional)">
            <input value={uin} onChange={(e) => setUin(e.target.value)} style={inputStyle} placeholder="DRN-01-999999" />
          </Field>
          <div style={{ display: 'flex', gap: '.5rem' }}>
            <Field label="Latitude">
              <input value={latitude} onChange={(e) => setLatitude(e.target.value)} style={inputStyle} />
            </Field>
            <Field label="Longitude">
              <input value={longitude} onChange={(e) => setLongitude(e.target.value)} style={inputStyle} />
            </Field>
          </div>
        </div>

        {error && <p style={{ color: 'var(--dm-status-alert)', fontSize: '.85rem' }}>{error}</p>}

        <div style={{ display: 'flex', gap: '.75rem', marginTop: '1.5rem', justifyContent: 'flex-end' }}>
          <button type="button" onClick={onClose} style={{ ...buttonStyle, background: 'transparent', border: '1px solid var(--dm-slate-500)' }}>
            Cancel
          </button>
          <button type="button" onClick={submit} disabled={submitting} style={{ ...buttonStyle, background: 'var(--dm-status-active)' }}>
            {submitting ? 'Saving…' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: '.3rem', fontSize: '.8rem', flex: 1, opacity: 0.9 }}>
      {label}
      {children}
    </label>
  )
}

const inputStyle: React.CSSProperties = {
  border: 0,
  borderRadius: 'var(--dm-radius-sm)',
  padding: '.55rem .7rem',
  background: 'var(--dm-surface-inset)',
  color: 'var(--dm-text-on-dark)',
  fontSize: '.9rem',
}

const buttonStyle: React.CSSProperties = {
  border: 0,
  borderRadius: 'var(--dm-radius-sm)',
  padding: '.6rem 1.2rem',
  color: 'white',
  textTransform: 'uppercase',
  fontSize: '.8rem',
}
