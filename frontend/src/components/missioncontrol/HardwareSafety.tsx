import { useEffect, useState } from 'react'
import { Hand, KeyRound, Route, ShieldAlert } from 'lucide-react'
import * as mc from '../../api/missionControl'
import type { FlightPermission, Prearm } from '../../api/missionControl'
import { listMissions } from '../../api/missions'
import type { Mission } from '../../api/types'
import { ApiError } from '../../api/client'
import type { LiveDrone } from './liveFleet'

const errText = (err: unknown) => (err instanceof ApiError ? `${err.code ?? err.status}: ${err.message}` : String(err))

/**
 * Real aircraft only. A person grants each flight (time-limited, with safety
 * acknowledgements), the RC safety pilot can take over at any moment, and a
 * pilot/autopilot override locks Drone MatrX out until it is released here.
 */
export function HardwareSafety({ drone, prearm, canWrite, busy, onStartMission, onChanged }: {
  drone: LiveDrone
  prearm: Prearm | null
  canWrite: boolean
  busy: boolean
  onStartMission: () => void
  onChanged: () => void
}) {
  const [permission, setPermission] = useState<FlightPermission | null>(null)
  const [acks, setAcks] = useState<Record<string, string>>({})
  const [granting, setGranting] = useState(false)
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const [minutes, setMinutes] = useState(10)
  const [missions, setMissions] = useState<Mission[]>([])
  const [missionId, setMissionId] = useState('')
  const [missionAlt, setMissionAlt] = useState(10)
  const [missionSpeed, setMissionSpeed] = useState(4)
  const [uploaded, setUploaded] = useState<string | null>(null)
  const [releaseReason, setReleaseReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const override = prearm?.checks.find((c) => c.id === 'pilot_override' && c.status !== 'PASS')
  const rc = prearm?.checks.find((c) => c.id === 'rc_link')

  const loadPermission = () => void mc.getFlightPermission(drone.id).then((r) => setPermission(r.permission)).catch(() => undefined)
  useEffect(() => {
    loadPermission()
    void mc.flightPermissionAcks().then((r) => setAcks(r.required)).catch(() => undefined)
    void listMissions().then(setMissions).catch(() => undefined)
    const t = window.setInterval(() => { setNow(Date.now()); loadPermission() }, 5000)
    return () => window.clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- per drone (the panel is keyed by drone id)
  }, [drone.id])

  const remaining = permission ? Math.max(0, Math.round((Date.parse(permission.expires_at) - now) / 1000)) : 0
  const active = !!permission && remaining > 0

  const run = async (fn: () => Promise<unknown>) => {
    setError(null)
    try {
      await fn()
      loadPermission()
      onChanged()
    } catch (err) {
      setError(errText(err))
    }
  }

  return (
    <div style={{ display: 'grid', gap: '.4rem', border: '1px solid #FF8A80', borderRadius: 4, padding: '.4rem' }} data-testid="hw-safety">
      <div className="dm-card-title" style={{ fontSize: '.7rem', margin: 0, color: '#FF8A80' }}><ShieldAlert size={12} /> Real aircraft safety</div>
      <div style={{ fontSize: '.64rem' }}>
        The RC safety pilot can take over at any time with the transmitter's mode switch. Drone MatrX never overrides RC input.
      </div>
      {rc && rc.status !== 'PASS' && (
        <div className="dm-banner dm-banner--bad" style={{ fontSize: '.66rem', textTransform: 'none', letterSpacing: 0 }} data-testid="hw-rc-warning">
          RC transmitter: {rc.status}. {rc.detail}
        </div>
      )}
      {override && (
        <div className="dm-banner dm-banner--bad" style={{ fontSize: '.66rem', textTransform: 'none', letterSpacing: 0, display: 'grid', gap: 4 }} data-testid="hw-override">
          <span><Hand size={12} /> <strong>PILOT / AUTOPILOT HAS CONTROL.</strong> {override.detail}. Drone MatrX refuses all automated commands until this is released.</span>
          {canWrite && (
            <span style={{ display: 'flex', gap: 4 }}>
              <input className="dm-input" placeholder="Why it is safe to release (e.g. pilot handed back control)" value={releaseReason}
                onChange={(e) => setReleaseReason(e.target.value)} style={{ flex: 1 }} aria-label="Release reason" data-testid="hw-release-reason" />
              <button type="button" className="dm-btn" disabled={releaseReason.trim().length < 3}
                onClick={() => void run(async () => { await mc.releaseOverride(drone.id, releaseReason.trim()); setReleaseReason('') })}
                data-testid="hw-release">Release</button>
            </span>
          )}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '.68rem', flexWrap: 'wrap' }} data-testid="hw-permission">
        <KeyRound size={12} />
        {active ? (
          <span style={{ color: '#7BD88F' }} data-testid="hw-permission-active">FLIGHT PERMITTED · {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, '0')} left</span>
        ) : (
          <span style={{ color: '#FFD08A' }} data-testid="hw-permission-none">NO FLIGHT PERMISSION: arm, take-off, go-to and missions are refused</span>
        )}
        {canWrite && !active && (
          <button type="button" className="dm-btn dm-btn--primary" style={{ marginLeft: 'auto' }} onClick={() => { setChecked({}); setGranting(true) }}
            disabled={!!override} data-testid="hw-grant-open">Grant flight permission</button>
        )}
        {canWrite && active && (
          <button type="button" className="dm-btn" style={{ marginLeft: 'auto' }} data-testid="hw-revoke"
            onClick={() => void run(() => mc.revokeFlightPermission(drone.id, 'revoked by operator'))}>Revoke</button>
        )}
      </div>

      {canWrite && (
        <div style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: '.66rem', flexWrap: 'wrap' }} data-testid="hw-mission">
          <Route size={12} />
          <select className="dm-input" value={missionId} onChange={(e) => setMissionId(e.target.value)} aria-label="Mission (CBBA plan)" data-testid="hw-mission-select"
            style={{ maxWidth: 170 }}>
            <option value="">CBBA mission…</option>
            {missions.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
          <label title="Give every aircraft its own altitude: no collision avoidance runs on real drones">Alt
            <input className="dm-input" type="number" min={5} max={120} value={missionAlt} onChange={(e) => setMissionAlt(Number(e.target.value))}
              style={{ width: 48, marginLeft: 3 }} data-testid="hw-mission-alt" /> m</label>
          <label>Speed
            <input className="dm-input" type="number" min={1} max={15} value={missionSpeed} onChange={(e) => setMissionSpeed(Number(e.target.value))}
              style={{ width: 42, marginLeft: 3 }} data-testid="hw-mission-speed" /> m/s</label>
          <button type="button" className="dm-btn" disabled={!missionId || drone.armed || busy} data-testid="hw-mission-upload"
            onClick={() => void run(async () => {
              const r = await mc.uploadHardwareMission(drone.id, missionId, missionAlt, missionSpeed)
              setUploaded(`${r.uploaded} items at ${r.altitude_m} m, ${r.speed_mps} m/s (${r.tasks.join(', ')})`)
            })}>Upload to aircraft</button>
          <button type="button" className="dm-btn dm-btn--armed" disabled={!uploaded || !active || busy || !!override} onClick={onStartMission}
            title={!active ? 'Needs a flight permission' : 'Arm and fly the uploaded mission'} data-testid="hw-mission-start">Start mission</button>
          {uploaded && <span className="dm-mono" data-testid="hw-mission-uploaded">{uploaded}</span>}
        </div>
      )}
      {error && <div className="dm-banner dm-banner--bad" role="alert" style={{ fontSize: '.66rem', textTransform: 'none', letterSpacing: 0 }} data-testid="hw-error">{error}</div>}

      {granting && (
        <div className="dm-dialog-backdrop" role="dialog" aria-modal="true" aria-label="Grant flight permission" data-testid="hw-grant">
          <div className="dm-dialog" style={{ width: 'min(32rem, 94vw)' }}>
            <h2 style={{ margin: 0, fontSize: '1.1rem', textTransform: 'uppercase' }}>Permit flight: {drone.name}</h2>
            <p style={{ margin: 0, fontSize: '.8rem' }}>
              REAL AIRCRAFT. Confirm every item. The permission is recorded with your name and expires automatically; revoke it at any time.
              {prearm && !prearm.can_arm && !drone.armed && <strong style={{ color: '#FF8A80' }}> Pre-arm is blocked ({prearm.blocking.join(', ')}): the permission will be refused.</strong>}
            </p>
            {Object.entries(acks).map(([k, label]) => (
              <label key={k} style={{ display: 'flex', gap: 6, alignItems: 'flex-start', fontSize: '.8rem' }}>
                <input type="checkbox" checked={!!checked[k]} onChange={(e) => setChecked({ ...checked, [k]: e.target.checked })} data-testid={`hw-ack-${k}`} />
                {label}
              </label>
            ))}
            <label style={{ fontSize: '.8rem' }}>Valid for (minutes)
              <input className="dm-input" type="number" min={1} max={30} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}
                style={{ width: 70, marginLeft: 6 }} data-testid="hw-grant-minutes" />
            </label>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button type="button" className="dm-btn dm-btn--ghost" onClick={() => setGranting(false)} data-testid="hw-grant-cancel">Cancel</button>
              <button type="button" className="dm-btn dm-btn--armed" data-testid="hw-grant-confirm"
                disabled={Object.keys(acks).some((k) => !checked[k]) || minutes < 1 || minutes > 30}
                onClick={() => void run(async () => {
                  await mc.grantFlightPermission(drone.id, minutes, checked, missionId || null, null)
                  setGranting(false)
                })}>Grant permission</button>
            </div>
            {error && <div className="dm-banner dm-banner--bad" style={{ fontSize: '.7rem', textTransform: 'none', letterSpacing: 0 }}>{error}</div>}
          </div>
        </div>
      )}
    </div>
  )
}
