import { useEffect, useMemo, useState } from 'react'
import { Crosshair, Home, Link2, Pause, PlaneLanding, PlaneTakeoff, Play, Power, RefreshCw, ShieldCheck } from 'lucide-react'
import * as mc from '../../api/missionControl'
import type { CommandName, DroneCommand, Prearm } from '../../api/missionControl'
import { ApiError } from '../../api/client'
import { useDroneStore } from '../../store/useDroneStore'
import { useMissionControlStore } from '../../store/useMissionControlStore'
import { SOURCE_LABEL, type LiveDrone } from './liveFleet'
import { HardwareSafety } from './HardwareSafety'

const FINAL = ['confirmed', 'rejected', 'timed_out', 'failed', 'unsupported', 'cancelled']

interface Props {
  drone: LiveDrone
  canWrite: boolean
  gotoTarget: [number, number] | null
  gotoPicking: boolean
  setGotoPicking: (v: boolean) => void
  clearGoto: () => void
  onBind: () => void
}

export function CommandStateTag({ c }: { c: DroneCommand }) {
  return <span className={`dm-cmd-state dm-cmd-state--${c.state}`} data-testid={`cmd-state-${c.command}`} data-state={c.state}>{c.state.toUpperCase()}</span>
}

export function DroneControl({ drone, canWrite, gotoTarget, gotoPicking, setGotoPicking, clearGoto, onBind }: Props) {
  const commands = useMissionControlStore((s) => s.commands)
  const trackCommand = useMissionControlStore((s) => s.trackCommand)
  const resyncVersion = useMissionControlStore((s) => s.resyncVersion)
  const [prearm, setPrearm] = useState<Prearm | null>(null)
  const [prearmErr, setPrearmErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<null | { command: CommandName; params: Record<string, unknown> }>(null)
  const [modeAck, setModeAck] = useState(false)
  const [gotoAlt, setGotoAlt] = useState(20)
  const [taskKey, setTaskKey] = useState('')
  const [assignNote, setAssignNote] = useState<string | null>(null)
  const sitl = drone.telemetry_source === 'sitl'
  const hw = drone.telemetry_source === 'hardware'
  const withFc = sitl || hw // a real flight controller (PX4 SITL or ArduPilot hardware)
  const fcMode = sitl ? 'sitl' : hw ? 'hardware' : undefined
  const hasCap = (c: string) => drone.capabilities.includes(c)
  const flying = withFc ? drone.armed && drone.altitude > 0.5 : drone.telemetry_source === 'kinematic_demo' && drone.flight_mode !== 'STANDBY'

  const mine = useMemo(() => Object.values(commands).filter((c) => c.drone_id === drone.id)
    .sort((a, b) => (b.requested_at ?? '').localeCompare(a.requested_at ?? '')).slice(0, 6), [commands, drone.id])

  const fetchPrearm = () => mc.getPrearm(drone.id, fcMode)
    .then((p) => { setPrearm(p); setPrearmErr(null) })
    .catch((err) => setPrearmErr(err instanceof Error ? err.message : 'Pre-arm unavailable'))
  const loadPrearm = () => void fetchPrearm()
  // The page keys this component by drone id, so state starts fresh per drone.
  useEffect(() => {
    if (withFc && drone.binding?.state === 'bound') void fetchPrearm()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload when the binding changes
  }, [drone.binding?.state])

  // Authoritative command state from the backend: on open, after a WebSocket
  // reconnect, and every 2 s while a command is still in flight (a missed
  // push must never leave a stale ACCEPTED on screen).
  const pending = mine.some((c) => !FINAL.includes(c.state))
  useEffect(() => {
    const load = () => void mc.listCommands({ droneId: drone.id }).then((rows) => rows.forEach(trackCommand)).catch(() => undefined)
    load()
    if (!pending) return
    const t = window.setInterval(load, 2000)
    return () => window.clearInterval(t)
  }, [drone.id, trackCommand, resyncVersion, pending])

  const [takeoffAfterArm, setTakeoffAfterArm] = useState(true)

  /** Telemetry (not the API answer) says the drone is armed. */
  const waitArmed = async (ms: number) => {
    const t0 = Date.now()
    while (Date.now() - t0 < ms) {
      if (useDroneStore.getState().drones.find((d) => d.id === drone.id)?.armed) return true
      await new Promise((r) => setTimeout(r, 200))
    }
    return false
  }

  const send = async (command: CommandName, params: Record<string, unknown> = {}) => {
    setBusy(command)
    setError(null)
    try {
      const c = await mc.sendCommand(drone.id, command, params, fcMode ?? drone.telemetry_source)
      trackCommand({ ...c, drone_name: drone.name })
      if (command === 'GOTO') clearGoto()
      // PX4 disarms on its own if no take-off follows within COM_DISARM_PRFLT: with
      // "take off once armed", TAKEOFF goes out as soon as telemetry confirms ARMED.
      if (command === 'ARM' && takeoffAfterArm && c.state === 'accepted') {
        if (await waitArmed(8000)) {
          const t = await mc.sendCommand(drone.id, 'TAKEOFF', { altitude_m: gotoAlt }, fcMode ?? null)
          trackCommand({ ...t, drone_name: drone.name })
        } else setError('ARM accepted, but telemetry did not show ARMED within 8 s: take-off not sent')
      }
    } catch (err) {
      const api = err instanceof ApiError ? err : null
      const cmd = api?.body?.command as DroneCommand | undefined
      if (cmd) trackCommand(cmd)
      setError(api ? `${api.code ?? api.status}: ${api.message}` : String(err))
      if (api?.body?.prearm) setPrearm(api.body.prearm as Prearm)
    } finally {
      setBusy(null)
      setConfirm(null)
      setModeAck(false)
    }
  }

  // Real aircraft and arming always need a confirmation; SITL quick commands do too for ARM/DISARM/LAND.
  const ask = (command: CommandName, params: Record<string, unknown> = {}) => {
    if (command === 'ARM' || command === 'DISARM' || command === 'LAND' || command === 'GOTO' || drone.telemetry_source === 'hardware') setConfirm({ command, params })
    else void send(command, params)
  }

  return (
    <div className="dm-card" style={{ background: 'var(--dm-slate-700)', padding: '.5rem', display: 'grid', gap: '.45rem' }} data-testid="mc-drone-control">
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <strong data-testid="mc-selected-name">{drone.name}</strong>
        <span className={`dm-src dm-src--${drone.telemetry_source}`}>{SOURCE_LABEL[drone.telemetry_source]}</span>
        <span className={`dm-conn dm-conn--${drone.connection_state}`}>{drone.connection_state.toUpperCase()}</span>
      </div>
      <div className="dm-mono" style={{ fontSize: '.66rem', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2px 8px' }}>
        <span>Mode {drone.flight_mode}</span><span data-testid="mc-armed">{drone.armed ? 'ARMED' : 'DISARMED'}</span>
        <span>Alt {drone.altitude.toFixed(1)} m</span><span>Batt {drone.battery.toFixed(0)}%</span>
        <span>{drone.latitude != null ? `${drone.latitude.toFixed(6)}` : '—'}</span><span>{drone.longitude != null ? `${drone.longitude.toFixed(6)}` : '—'}</span>
        {drone.binding && <span style={{ gridColumn: '1 / -1' }} data-testid="mc-binding">FC {drone.binding.fc_identity} · sysid {drone.binding.system_id ?? '?'} · {drone.binding.firmware ?? 'firmware ?'} · {drone.binding.state.toUpperCase()}</span>}
      </div>
      {drone.telemetry_source === 'kinematic_demo' && (
        <div className="dm-banner dm-banner--amber" style={{ fontSize: '.66rem', textTransform: 'none', letterSpacing: 0 }}>Sprint 4 kinematic demo drone: no flight controller. HOLD / RTL / RESUME only, acknowledged by the command bus and confirmed from telemetry.</div>
      )}
      {!drone.binding && (drone.simulated || drone.hardware) && canWrite && (
        <button type="button" className="dm-btn dm-btn--primary" onClick={onBind} data-testid="mc-bind-open"><Link2 size={13} /> Bind a flight controller</button>
      )}
      {error && <div className="dm-banner dm-banner--bad" role="alert" data-testid="mc-cmd-error" style={{ fontSize: '.68rem', textTransform: 'none', letterSpacing: 0 }}>{error}</div>}

      {withFc && drone.binding?.state === 'bound' && (
        <div data-testid="mc-prearm">
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span className="dm-card-title" style={{ fontSize: '.7rem', margin: 0 }}><ShieldCheck size={12} /> Pre-arm checklist</span>
            {prearm && <span className="dm-mono" style={{ fontSize: '.62rem', color: prearm.can_arm ? '#7BD88F' : '#FF8A80' }} data-testid="mc-prearm-verdict">{prearm.can_arm ? 'READY TO ARM' : `BLOCKED (${prearm.blocking.length})`}</span>}
            <button type="button" className="dm-btn dm-btn--ghost" style={{ marginLeft: 'auto', padding: '.1rem .35rem' }} onClick={loadPrearm} aria-label="Re-run pre-arm checks" data-testid="mc-prearm-refresh"><RefreshCw size={11} /></button>
          </div>
          {prearmErr && <p className="dm-error" style={{ fontSize: '.66rem' }}>{prearmErr}</p>}
          {prearm?.checks.map((c) => (
            <div key={c.id} className="dm-check-row" data-testid={`prearm-${c.id}`} data-status={c.status}>
              <span className={`dm-check-status dm-check-status--${c.status}`}>{c.status}{c.required ? '' : '*'}</span>
              <span><strong>{c.label}</strong> <span style={{ opacity: 0.75 }}>{c.detail}</span></span>
            </div>
          ))}
        </div>
      )}

      {hw && drone.binding?.state === 'bound' && (
        <HardwareSafety drone={drone} prearm={prearm} canWrite={canWrite} busy={!!busy} onChanged={loadPrearm}
          onStartMission={() => ask('MISSION_START')} />
      )}

      {canWrite && (drone.telemetry_source === 'sitl' || drone.telemetry_source === 'kinematic_demo' || drone.telemetry_source === 'hardware') && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }} data-testid="mc-quick-actions">
          {hasCap('ARM') && !drone.armed && (
            <button type="button" className="dm-btn dm-btn--armed" disabled={!!busy || !prearm?.can_arm} onClick={() => ask('ARM')} data-testid="mc-arm"
              title={prearm?.can_arm ? `Arm (${fcMode?.toUpperCase()})` : `Blocked: ${prearm?.blocking.join(', ') ?? 'pre-arm not run'}`}><Power size={12} /> Arm</button>
          )}
          {hasCap('DISARM') && drone.armed && <button type="button" className="dm-btn" disabled={!!busy || flying} onClick={() => ask('DISARM')} data-testid="mc-disarm" title={flying ? 'Only on the ground' : 'Disarm'}><Power size={12} /> Disarm</button>}
          {hasCap('TAKEOFF') && drone.armed && !flying && <button type="button" className="dm-btn" disabled={!!busy} onClick={() => void send('TAKEOFF', { altitude_m: gotoAlt })} data-testid="mc-takeoff"><PlaneTakeoff size={12} /> Take off {gotoAlt} m</button>}
          {hasCap('HOLD') && <button type="button" className="dm-btn" disabled={!!busy || !flying} onClick={() => ask('HOLD')} data-testid="mc-hold"><Pause size={12} /> Hold</button>}
          {hasCap('RTL') && <button type="button" className="dm-btn" disabled={!!busy || !flying} onClick={() => ask('RTL')} data-testid="mc-rtl"><Home size={12} /> RTL</button>}
          {hasCap('RESUME') && <button type="button" className="dm-btn" disabled={!!busy || drone.flight_mode !== 'HOLD'} onClick={() => ask('RESUME')} data-testid="mc-resume"><Play size={12} /> Resume</button>}
          {hasCap('LAND') && <button type="button" className="dm-btn" disabled={!!busy || !flying} onClick={() => ask('LAND')} data-testid="mc-land"><PlaneLanding size={12} /> Land</button>}
          {hasCap('GOTO') && (
            <button type="button" className="dm-btn" disabled={!!busy || !flying} onClick={() => setGotoPicking(!gotoPicking)} aria-pressed={gotoPicking} data-testid="mc-goto-pick"
              style={{ outline: gotoPicking ? '2px solid #FFD08A' : undefined }}><Crosshair size={12} /> {gotoPicking ? 'Click the map…' : 'Go to'}</button>
          )}
        </div>
      )}
      {sitl && drone.armed && !flying && (
        <div style={{ fontSize: '.64rem', color: '#FFD08A' }} data-testid="mc-disarm-hint">PX4 disarms automatically if no take-off follows within its preflight timeout (COM_DISARM_PRFLT, 10 s by default).</div>
      )}
      {gotoTarget && hasCap('GOTO') && canWrite && (
        <div style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: '.66rem' }} data-testid="mc-goto-panel">
          <span className="dm-mono">{gotoTarget[0].toFixed(6)}, {gotoTarget[1].toFixed(6)}</span>
          <input className="dm-input" type="number" min={5} max={120} value={gotoAlt} onChange={(e) => setGotoAlt(Number(e.target.value))} style={{ width: 56 }} aria-label="Go-to altitude (m)" />
          <button type="button" className="dm-btn dm-btn--primary" disabled={!!busy} onClick={() => ask('GOTO', { latitude: gotoTarget[0], longitude: gotoTarget[1], altitude_m: gotoAlt })} data-testid="mc-goto-send">Send GO TO</button>
          <button type="button" className="dm-btn dm-btn--ghost" onClick={clearGoto}>Clear</button>
        </div>
      )}
      {canWrite && drone.mission && (
        <div style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: '.66rem' }}>
          <input className="dm-input" placeholder="Task (e.g. T003)" value={taskKey} onChange={(e) => setTaskKey(e.target.value.toUpperCase())} style={{ width: 110 }} aria-label="Task to assign" data-testid="mc-task-key" />
          <button type="button" className="dm-btn" disabled={!taskKey || !!busy} data-testid="mc-assign-task" onClick={async () => {
            setBusy('ASSIGN')
            setError(null)
            try {
              const r = await mc.assignTask(drone.id, drone.mission!.mission_id, taskKey)
              setAssignNote(r.changed ? `${taskKey} assigned to ${drone.name}` : `${taskKey} was already assigned to ${drone.name}`)
            } catch (err) {
              setError(err instanceof Error ? err.message : String(err))
            } finally {
              setBusy(null)
            }
          }}>Assign task</button>
          {assignNote && <span className="dm-mono" style={{ opacity: 0.85 }} data-testid="mc-assign-result">{assignNote}</span>}
        </div>
      )}

      {mine.length > 0 && (
        <div style={{ display: 'grid', gap: 3 }} data-testid="mc-command-log">
          {mine.map((c) => (
            <div key={c.id} style={{ display: 'flex', gap: 5, alignItems: 'center', fontSize: '.64rem', flexWrap: 'wrap' }} data-testid={`mc-cmd-${c.id}`}>
              <strong className="dm-mono">{c.command}</strong>
              <CommandStateTag c={c} />
              {!FINAL.includes(c.state) && <span style={{ opacity: 0.7 }}>awaiting telemetry…</span>}
              {c.timings?.request_to_ack_ms != null && <span className="dm-mono" style={{ opacity: 0.7 }}>ack {Number(c.timings.request_to_ack_ms).toFixed(0)} ms</span>}
              {c.timings?.observed_state_ms != null && <span className="dm-mono" style={{ opacity: 0.7 }}>state {(Number(c.timings.observed_state_ms) / 1000).toFixed(1)} s</span>}
              {c.failure_reason && <span style={{ color: '#FF8A80' }}>{c.failure_reason}</span>}
            </div>
          ))}
        </div>
      )}

      {confirm && (
        <div className="dm-dialog-backdrop" role="dialog" aria-modal="true" aria-label={`Confirm ${confirm.command}`} data-testid="mc-confirm">
          <div className="dm-dialog" style={{ width: 'min(28rem, 94vw)' }}>
            <h2 style={{ margin: 0, fontSize: '1.1rem', textTransform: 'uppercase' }}>{confirm.command} {drone.name}?</h2>
            <p style={{ margin: 0, fontSize: '.85rem' }}>
              Target: <strong>{drone.name}</strong> · <span className={`dm-src dm-src--${drone.telemetry_source}`}>{SOURCE_LABEL[drone.telemetry_source]}</span>
              {confirm.command === 'GOTO' && <> · to {Number(confirm.params.latitude).toFixed(6)}, {Number(confirm.params.longitude).toFixed(6)} at {String(confirm.params.altitude_m)} m</>}
            </p>
            {confirm.command === 'ARM' && <p style={{ margin: 0, fontSize: '.8rem' }}>The backend re-runs the pre-arm checklist immediately before sending ARM. The drone is reported armed only when its telemetry says so.</p>}
            {hw && <p style={{ margin: 0, fontSize: '.8rem', color: '#FF8A80' }} data-testid="mc-confirm-hw">REAL AIRCRAFT: propellers can spin. The RC safety pilot must be ready to take over; switching the transmitter's flight mode takes control from Drone MatrX immediately.</p>}
            {confirm.command === 'MISSION_START' && <p style={{ margin: 0, fontSize: '.8rem' }}>Arms the aircraft and flies the uploaded CBBA mission from its take-off item (AUTO mode), then returns to launch.</p>}
            {confirm.command === 'ARM' && (
              <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: '.8rem' }}>
                <input type="checkbox" checked={takeoffAfterArm} onChange={(e) => setTakeoffAfterArm(e.target.checked)} data-testid="mc-confirm-takeoff" />
                Take off to {gotoAlt} m once telemetry shows ARMED (the autopilot disarms again if no take-off follows)
              </label>
            )}
            <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: '.8rem' }}>
              <input type="checkbox" checked={modeAck} onChange={(e) => setModeAck(e.target.checked)} data-testid="mc-confirm-mode" />
              I confirm this is a {drone.telemetry_source === 'sitl' ? 'SIMULATED (PX4 SITL)' : drone.telemetry_source === 'hardware' ? 'REAL HARDWARE' : 'KINEMATIC DEMO'} drone
            </label>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button type="button" className="dm-btn dm-btn--ghost" onClick={() => { setConfirm(null); setModeAck(false) }} data-testid="mc-confirm-cancel">Cancel</button>
              <button type="button" className={`dm-btn ${confirm.command === 'ARM' ? 'dm-btn--armed' : 'dm-btn--primary'}`} disabled={!modeAck || !!busy}
                onClick={() => void send(confirm.command, confirm.params)} data-testid="mc-confirm-ok">{confirm.command}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
