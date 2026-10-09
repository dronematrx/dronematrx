import { AlertTriangle, Play, Square } from 'lucide-react'
import type { OrcaEvent, OrcaView } from '../../api/types'
import { nameOf } from '../../lib/opsFormat'

const EVENT_LABEL: Record<string, string> = {
  conflict_predicted: 'Potential collision detected',
  constraint_generated: 'ORCA constraint generated',
  avoidance_applied: 'Avoidance velocity applied',
  avoidance_released: 'Back on planned velocity',
  conflict_cleared: 'Conflict cleared',
  rth_queued: 'RTH requested (queued)',
  rth_granted: 'RTH granted · lowest band',
  rth_landed: 'Landed at home',
  rth_complete: 'Recharged · back in service',
  band_full_hold: 'All altitude bands full · HOLD',
}


/** Amber alert driven by the engine's time-to-collision predictions. */
export function PredictionBanner({ view }: { view: OrcaView }) {
  if (view.predictions.length === 0) return null
  const soonest = [...view.predictions].sort((a, b) => a.ttc_s - b.ttc_s)
  return (
    <div className="dm-banner dm-banner--amber" role="alert" data-testid="orca-prediction-alert">
      <AlertTriangle size={18} />
      <span style={{ display: 'grid', gap: 2 }}>
        <span>Collision predicted</span>
        {soonest.map((p) => (
          <span key={`${p.a_id}-${p.b_id}`} className="dm-mono" style={{ fontSize: '.75rem', fontWeight: 400, letterSpacing: 0 }} data-testid="orca-prediction">
            {p.a} ↔ {p.b} · TTC {p.ttc_s.toFixed(1)} s
          </span>
        ))}
      </span>
    </div>
  )
}

export function OrcaStatsCard({ view }: { view: OrcaView }) {
  const t = view.timing
  const rows: [string, string][] = [
    ['Update rate', `${t.measured_hz?.toFixed(2) ?? '--'} / ${t.target_hz} Hz`],
    ['Missed cycles', `${t.missed_cycles} / ${t.cycles}`],
    ['Compute avg · max', `${t.compute_ms_avg?.toFixed(2) ?? '--'} · ${t.compute_ms_max?.toFixed(2) ?? '--'} ms`],
    ['Resolution max', t.resolution_ms_max !== null ? `${t.resolution_ms_max.toFixed(2)} ms` : '--'],
    ['Active conflicts', String(view.predictions.length)],
    ['State source', view.source === 'nats' ? 'NATS JetStream' : 'direct (NATS down)'],
  ]
  return (
    <div className="dm-card" data-testid="orca-stats">
      <div className="dm-card-title">ORCA Engine&hellip;</div>
      {rows.map(([k, v]) => (
        <div key={k} className="dm-kv">
          <span>{k}</span>
          <span className="dm-mono">{v}</span>
        </div>
      ))}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.2rem .6rem', marginTop: '.5rem', fontSize: '.7rem' }} aria-label="Map legend">
        {(
          [
            ['--dm-orca-velocity', 'solid', 'Velocity (6 s)'],
            ['--dm-orca-preferred', 'dashed', 'Preferred (plan)'],
            ['--dm-orca-avoidance', 'solid', 'ORCA safe velocity'],
            ['--dm-orca-alert', 'dashed', 'Predicted collision'],
          ] as const
        ).map(([token, style, label]) => (
          <span key={label} style={{ display: 'flex', alignItems: 'center', gap: '.35rem' }}>
            <span style={{ width: 18, borderTop: `3px ${style} var(${token})` }} />
            {label}
          </span>
        ))}
      </div>
    </div>
  )
}

export function RthQueueCard({ view }: { view: OrcaView }) {
  const { active, queued, max_concurrent } = view.rth_queue
  const bands = Object.entries(view.bands)
  return (
    <div className="dm-card" data-testid="rth-queue">
      <div className="dm-card-title">RTH Queue &amp; Bands&hellip;</div>
      <div className="dm-kv">
        <span>Returning ({active.length}/{max_concurrent})</span>
        <span className="dm-mono">{active.map((a) => `${nameOf(view, a.drone_id)} (${a.reason})`).join(', ') || '—'}</span>
      </div>
      <div className="dm-kv">
        <span>Waiting</span>
        <span className="dm-mono">{queued.map((q) => `#${q.position} ${nameOf(view, q.drone_id)}`).join(', ') || '—'}</span>
      </div>
      {bands.map(([band, count]) => (
        <div key={band} className="dm-kv">
          <span>Band {band}{band === '0' ? ' (RTH)' : ''}</span>
          <span className="dm-mono">
            {view.band_altitudes[band]} m · {count} drone{count === 1 ? '' : 's'}
          </span>
        </div>
      ))}
    </div>
  )
}

export function OrcaTimeline({ events, view, limit = 14 }: { events: OrcaEvent[]; view: OrcaView | null; limit?: number }) {
  const shown = events.filter((e) => e.kind !== 'avoidance_released').slice(-limit).reverse()
  return (
    <div className="dm-card" data-testid="orca-timeline">
      <div className="dm-card-title">ORCA Event Timeline&hellip;</div>
      {shown.length === 0 && <p style={{ fontSize: '.75rem', opacity: 0.7, margin: 0 }}>No ORCA events yet.</p>}
      <div className="dm-timeline">
        {shown.map((e) => (
          <div key={e.seq} className={`dm-timeline-row dm-timeline-row--${e.kind}`} data-testid={`timeline-${e.kind}`}>
            <span>{new Date(e.wall_time).toLocaleTimeString([], { hour12: false })}</span>
            <span>
              {EVENT_LABEL[e.kind] ?? e.kind}
              <br />
              <span style={{ opacity: 0.8 }}>
                {e.drones.map((id) => nameOf(view, id)).join(' ↔ ')}
                {typeof e.details.time_to_collision_s === 'number' && ` · TTC ${e.details.time_to_collision_s.toFixed(1)} s`}
                {typeof e.details.min_separation_m === 'number' && ` · min sep ${e.details.min_separation_m.toFixed(2)} m`}
              </span>
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function DemoControls({ view, canWrite, busy, onStart, onStop }: { view: OrcaView | null; canWrite: boolean; busy: boolean; onStart: () => void; onStop: () => void }) {
  const demo = view?.demo
  return (
    <div className="dm-card" data-testid="orca-demo">
      <div className="dm-card-title">8-Drone Demo Scenario&hellip;</div>
      <p style={{ fontSize: '.75rem', opacity: 0.8, margin: '0 0 .5rem' }}>
        Head-on + crossing conflicts (T+10), Wi-Fi → LTE (T+20), LTE → LoRa (T+30), recovery (T+40).
      </p>
      {demo?.running && (
        <p className="dm-mono" style={{ fontSize: '.75rem', margin: '0 0 .5rem' }} data-testid="demo-elapsed">
          T+{demo.elapsed_s?.toFixed(0)} s · {demo.timeline.at(-1)?.step}
        </p>
      )}
      {canWrite && (
        <div style={{ display: 'flex', gap: '.4rem' }}>
          <button type="button" className="dm-btn dm-btn--primary" disabled={busy} onClick={onStart} data-testid="demo-start">
            <Play size={14} /> {demo?.running ? 'Restart' : 'Start'}
          </button>
          <button type="button" className="dm-btn" disabled={busy || !demo?.running} onClick={onStop} data-testid="demo-stop">
            <Square size={14} /> Stop
          </button>
        </div>
      )}
    </div>
  )
}
