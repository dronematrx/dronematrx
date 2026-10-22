import { useState } from 'react'
import { Ban, CloudLightning, Pause, Play, Skull, Square, Wifi } from 'lucide-react'
import type { Drone } from '../../api/types'
import type { FailureType, SimEvent, SimInstance, SimSnapshot, SimSpeed } from '../../api/simulations'
import { formatDuration, formatPct } from '../../lib/format'
import { STALE_MS } from './SimMapLayers'

const SPEEDS: SimSpeed[] = [1, 2, 5, 10]

export function SimulationBadge() {
  return (
    <span className="dm-mono" data-testid="simulation-badge"
      style={{ background: '#8A6D3B', color: '#fff', padding: '.2rem .55rem', borderRadius: 'var(--dm-radius-pill)', fontSize: '.72rem', letterSpacing: '.08em', fontWeight: 700 }}>
      SIMULATION
    </span>
  )
}

interface ControlsProps {
  live: SimSnapshot | null
  state: string
  busy: string | null
  canWrite: boolean
  onPause: () => void
  onResume: () => void
  onSpeed: (s: SimSpeed) => void
  onStop: () => void
  onAbort: () => void
}

export function SimControls({ live, state, busy, canWrite, onPause, onResume, onSpeed, onStop, onAbort }: ControlsProps) {
  const p = live?.progress
  const paused = live?.paused || state === 'paused'
  const g = live?.guardrail ?? {}
  return (
    <div className="dm-card" style={{ background: 'var(--dm-navy-900)', display: 'grid', gap: '.5rem' }} data-testid="sim-controls">
      <div className="dm-stat-grid" style={{ marginBottom: 0, gridTemplateColumns: 'repeat(3, 1fr)' }}>
        <div className="dm-stat"><span>Sim time</span><strong data-testid="sim-time">{formatDuration(live?.sim_time_s ?? 0)}</strong></div>
        <div className="dm-stat"><span>Speed</span><strong data-testid="sim-speed-value">{live?.speed ?? 1}×</strong></div>
        <div className="dm-stat"><span>State</span><strong data-testid="sim-state">{(live?.state ?? state).toUpperCase()}</strong></div>
        <div className="dm-stat"><span>Tasks</span><strong data-testid="sim-tasks-progress">{p ? `${p.tasks_completed}/${p.tasks_assigned}` : '--'}</strong></div>
        <div className="dm-stat"><span>Coverage</span><strong data-testid="sim-coverage">{formatPct(p?.coverage_pct, 1)}</strong></div>
        <div className="dm-stat"><span>Sim FPS / RTF</span><strong data-testid="sim-fps">{g.sim_fps != null ? g.sim_fps.toFixed(0) : '--'} / {g.achieved_rtf != null ? g.achieved_rtf.toFixed(2) : '--'}</strong></div>
      </div>
      {p && (
        <div className="dm-batt" style={{ height: 8 }} aria-label="mission progress">
          <div style={{ width: `${p.tasks_assigned ? (100 * p.tasks_completed) / p.tasks_assigned : 0}%`, background: 'var(--dm-lane-2)' }} />
        </div>
      )}
      {g.active && <div className="dm-banner dm-banner--amber" data-testid="sim-guardrail">FPS guardrail: simulation below 10 FPS, speed reduced</div>}
      <div style={{ display: 'flex', gap: '.35rem', flexWrap: 'wrap' }}>
        {paused ? (
          <button type="button" className="dm-btn dm-btn--primary" onClick={onResume} disabled={!canWrite || busy !== null} data-testid="sim-resume"><Play size={13} /> Resume</button>
        ) : (
          <button type="button" className="dm-btn" onClick={onPause} disabled={!canWrite || busy !== null || state !== 'running'} data-testid="sim-pause"><Pause size={13} /> Pause</button>
        )}
        {SPEEDS.map((s) => (
          <button key={s} type="button" className={`dm-btn ${live?.speed === s ? 'dm-btn--primary' : 'dm-btn--ghost'}`} onClick={() => onSpeed(s)}
            disabled={!canWrite || busy !== null} data-testid={`sim-speed-${s}`}>{s}×</button>
        ))}
        <button type="button" className="dm-btn" onClick={onStop} disabled={!canWrite || busy !== null} data-testid="sim-stop"><Square size={13} /> Stop</button>
        <button type="button" className="dm-btn dm-btn--danger" onClick={onAbort} disabled={!canWrite || busy !== null} data-testid="sim-abort"><Ban size={13} /> Abort</button>
      </div>
    </div>
  )
}

export function SimDroneTable({ instances, live, drones, now }: { instances: SimInstance[]; live: SimSnapshot | null; drones: Drone[]; now: number }) {
  const byUin = new Map((live?.drones ?? []).map((d) => [d.uin, d]))
  return (
    <table className="dm-data-table" data-testid="sim-drone-table">
      <thead><tr><th>Drone</th><th>State</th><th>Mode</th><th>Batt</th><th>Alt</th><th>Spd</th><th>Task</th><th>Link</th></tr></thead>
      <tbody>
        {instances.map((inst) => {
          const d = drones.find((x) => x.id === inst.drone_id)
          const l = byUin.get(inst.uin)
          const stale = d?.last_telemetry_at ? now - Date.parse(d.last_telemetry_at) > STALE_MS : true
          return (
            <tr key={inst.uin} data-testid={`sim-row-${inst.uin}`} data-lat={d?.latitude ?? ''} data-lon={d?.longitude ?? ''}>
              <td>{inst.name}</td>
              <td className="dm-mono" style={{ color: l?.state === 'failed' ? '#FF8A80' : l?.state === 'comm_lost' ? '#FFD08A' : undefined }}>{l?.state ?? inst.state}</td>
              <td className="dm-mono">{d?.flight_mode ?? '--'}</td>
              <td className="dm-mono">{d ? `${d.battery.toFixed(0)}%` : '--'}</td>
              <td className="dm-mono">{d ? d.altitude.toFixed(1) : '--'}</td>
              <td className="dm-mono">{d ? d.speed.toFixed(1) : '--'}</td>
              <td className="dm-mono">{l?.current_task ?? '—'}</td>
              <td className="dm-mono">{stale ? 'STALE' : `${(d?.link_quality ?? 0).toFixed(0)}%`}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

interface FailureProps {
  instances: SimInstance[]
  busy: string | null
  canWrite: boolean
  obstaclePicking: boolean
  setObstaclePicking: (v: boolean) => void
  onInject: (type: FailureType, droneId: string | null, params: Record<string, unknown>) => void
}

/** Simulation-only failure injection (rendered only in simulation mode). */
export function FailurePanel({ instances, busy, canWrite, obstaclePicking, setObstaclePicking, onInject }: FailureProps) {
  const [droneId, setDroneId] = useState(instances[0]?.drone_id ?? '')
  const [latency, setLatency] = useState(300)
  const [loss, setLoss] = useState(20)
  const [battery, setBattery] = useState(20)
  const [wind, setWind] = useState({ speed: 12, from: 270, duration: 10 })
  const dis = !canWrite || busy !== null
  return (
    <div className="dm-card" style={{ background: 'var(--dm-navy-900)', display: 'grid', gap: '.5rem' }} data-testid="sim-failure-panel">
      <div className="dm-card-title" style={{ fontSize: '.8rem' }}>Failure injection · simulation only</div>
      <label className="dm-field"><span>Target drone</span>
        <select className="dm-input" value={droneId} onChange={(e) => setDroneId(e.target.value)} data-testid="sim-failure-drone">
          {instances.map((i) => <option key={i.drone_id} value={i.drone_id}>{i.name}</option>)}
        </select></label>
      <div style={{ display: 'flex', gap: '.35rem', flexWrap: 'wrap' }}>
        <button type="button" className="dm-btn dm-btn--danger" disabled={dis} onClick={() => onInject('kill', droneId, {})} data-testid="sim-kill"><Skull size={13} /> Kill drone (SITL)</button>
        <button type="button" className="dm-btn" disabled={dis} onClick={() => onInject('gps_drift', droneId, { enabled: true })} data-testid="sim-gps-drift">GPS drift on</button>
        <button type="button" className="dm-btn dm-btn--ghost" disabled={dis} onClick={() => onInject('gps_drift', droneId, { enabled: false })}>GPS drift off</button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.4rem' }}>
        <label className="dm-field"><span>Latency {latency} ms</span><input type="range" min={0} max={2000} step={50} value={latency} onChange={(e) => setLatency(Number(e.target.value))} data-testid="sim-latency" /></label>
        <label className="dm-field"><span>Packet loss {loss}%</span><input type="range" min={0} max={100} step={5} value={loss} onChange={(e) => setLoss(Number(e.target.value))} data-testid="sim-loss" /></label>
      </div>
      <div style={{ display: 'flex', gap: '.35rem', flexWrap: 'wrap' }}>
        <button type="button" className="dm-btn" disabled={dis} onClick={() => onInject('link', droneId, { latency_ms: latency, loss_pct: loss, down: false })} data-testid="sim-degrade-link"><Wifi size={13} /> Apply link quality</button>
        <button type="button" className="dm-btn dm-btn--danger" disabled={dis} onClick={() => onInject('link', droneId, { down: true })} data-testid="sim-link-down">Link down</button>
        <button type="button" className="dm-btn dm-btn--ghost" disabled={dis} onClick={() => onInject('link', droneId, { down: false, latency_ms: 0, loss_pct: 0 })} data-testid="sim-link-restore">Restore link</button>
      </div>
      <div style={{ display: 'flex', gap: '.35rem', alignItems: 'end' }}>
        <label className="dm-field" style={{ flex: 1 }}><span>Force battery to {battery}%</span><input type="range" min={5} max={60} value={battery} onChange={(e) => setBattery(Number(e.target.value))} /></label>
        <button type="button" className="dm-btn" disabled={dis} onClick={() => onInject('battery', droneId, { target_pct: battery, drain_s: 20 })} data-testid="sim-low-battery">Force low battery</button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '.3rem' }}>
        <label className="dm-field"><span>Wind m/s</span><input className="dm-input" type="number" min={1} max={30} value={wind.speed} onChange={(e) => setWind({ ...wind, speed: Number(e.target.value) })} /></label>
        <label className="dm-field"><span>From °</span><input className="dm-input" type="number" min={0} max={359} value={wind.from} onChange={(e) => setWind({ ...wind, from: Number(e.target.value) })} /></label>
        <label className="dm-field"><span>For s</span><input className="dm-input" type="number" min={1} max={120} value={wind.duration} onChange={(e) => setWind({ ...wind, duration: Number(e.target.value) })} /></label>
      </div>
      <div style={{ display: 'flex', gap: '.35rem', flexWrap: 'wrap' }}>
        <button type="button" className="dm-btn" disabled={dis} onClick={() => onInject('wind', null, { speed_mps: wind.speed, from_deg: wind.from, duration_s: wind.duration })} data-testid="sim-wind"><CloudLightning size={13} /> Wind gust (all)</button>
        <button type="button" className={`dm-btn ${obstaclePicking ? 'dm-btn--armed' : ''}`} disabled={dis} onClick={() => setObstaclePicking(!obstaclePicking)} data-testid="sim-obstacle">
          {obstaclePicking ? 'Click the map to place…' : 'Spawn obstacle'}
        </button>
      </div>
    </div>
  )
}

const EVENT_TONE: Record<string, string> = {
  process_crashed: '#FF8A80', drone_unavailable: '#FF8A80', aborted: '#FF8A80', failed: '#FF8A80', collision: '#FF8A80', simulator_failed: '#FF8A80', model_removed: '#FF8A80',
  comm_lost: '#FFD08A', comm_degraded: '#FFD08A', battery_low: '#FFD08A', gps_error: '#FFD08A', guardrail: '#FFD08A', drone_left_mission: '#FFD08A',
  tasks_reallocated: '#4CD0D0', task_completed: '#5CC28A', mission_completed: '#5CC28A', drone_mission_complete: '#5CC28A',
}

export function EventTimeline({ events, instances }: { events: SimEvent[]; instances: SimInstance[] }) {
  const name = (id: string | null) => instances.find((i) => i.drone_id === id)?.name ?? ''
  const rows = [...events].reverse().slice(0, 80)
  return (
    <div className="dm-timeline" style={{ maxHeight: 300, overflow: 'auto' }} data-testid="sim-event-timeline">
      {rows.map((e) => (
        <div key={e.id} className="dm-timeline-row" style={{ borderLeftColor: EVENT_TONE[e.event_type] ?? 'var(--dm-slate-500)' }} data-testid={`sim-event-${e.event_type}`}>
          <span>{e.sim_time_s != null ? `t+${e.sim_time_s.toFixed(1)}s` : new Date(e.created_at).toLocaleTimeString()}</span>
          <span>
            <strong>{e.event_type}</strong> {name(e.drone_id)} {e.previous_state || e.new_state ? `${e.previous_state ?? ''}→${e.new_state ?? ''}` : ''}
            {e.mission_outcome ? ` [${e.mission_outcome}]` : ''}
            <br />
            <span style={{ opacity: 0.8 }}>{e.response}</span>
          </span>
        </div>
      ))}
    </div>
  )
}

/** Process-manager view of the running fleet (PX4 instance, PID, MAVLink port, Gazebo model). */
export function SitlFleetTable({ instances }: { instances: SimInstance[] }) {
  return (
    <table className="dm-data-table" data-testid="sim-sitl-table">
      <thead><tr><th>Drone</th><th>PX4 -i</th><th>PID</th><th>MAVLink</th><th>Gazebo model</th><th>State</th></tr></thead>
      <tbody>
        {instances.map((i) => (
          <tr key={i.uin}>
            <td>{i.name}</td>
            <td className="dm-mono">{i.instance_index}</td>
            <td className="dm-mono">{i.sitl_pid ?? '--'}</td>
            <td className="dm-mono">{i.mavlink_port ? `udp ${i.mavlink_port}` : '--'}</td>
            <td className="dm-mono">{i.gz_model_name ?? '--'}</td>
            <td className="dm-mono">{i.state}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
