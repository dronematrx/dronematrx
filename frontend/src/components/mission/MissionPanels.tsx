import { useState } from 'react'
import { ChevronRight, Wifi, WifiOff } from 'lucide-react'
import type { Bundle, MissionPlan, ValidationReport, WizardState } from '../../api/types'
import { formatDistance, formatDuration, formatPct } from '../../lib/format'
import { laneToken } from '../../lib/tokens'
import { StatusPill } from '../ui/StatusPill'

/** Battery bar: current SOC, consumption, landing SOC and the RTH reserve marker. */
export function BatteryIndicator({ bundle }: { bundle: Bundle }) {
  const end = Math.max(0, bundle.battery_end_pct)
  return (
    <div title={`${bundle.battery_start_pct.toFixed(0)}% → ${end.toFixed(0)}% (reserve ${bundle.reserve_pct}%)`}>
      <div className="dm-batt" aria-label={`Battery ${bundle.battery_start_pct.toFixed(0)} percent, lands at ${end.toFixed(0)} percent`}>
        <div style={{ left: 0, width: `${end}%`, background: bundle.battery_warning ? 'var(--dm-status-armed)' : 'var(--dm-status-active)' }} />
        <div style={{ left: `${end}%`, width: `${Math.max(0, bundle.battery_start_pct - end)}%`, background: 'var(--dm-slate-400)' }} />
        <div style={{ left: `${bundle.reserve_pct}%`, width: 2, background: 'var(--dm-geo-nfz)' }} />
      </div>
      <div className="dm-mono" style={{ fontSize: '.65rem', opacity: 0.8, display: 'flex', justifyContent: 'space-between', marginTop: 2 }}>
        <span>{bundle.battery_start_pct.toFixed(0)}%</span>
        <span>−{bundle.energy_pct.toFixed(1)}%</span>
        <span style={{ color: bundle.battery_warning ? '#FFD08A' : undefined }}>
          {end.toFixed(0)}%{bundle.battery_warning ? ' ⚠' : ''}
        </span>
      </div>
    </div>
  )
}

interface LaneListProps {
  bundles: Bundle[]
  selectedDroneId: string | null
  onSelect: (droneId: string | null) => void
  onRemove?: (droneId: string) => void
  busy?: boolean
}

export function LaneList({ bundles, selectedDroneId, onSelect, onRemove, busy }: LaneListProps) {
  if (bundles.length === 0) return <p style={{ fontSize: '.8rem', opacity: 0.7 }}>No drone has assigned tasks.</p>
  return (
    <div style={{ display: 'grid', gap: '.4rem' }} data-testid="lane-list">
      {bundles.map((b) => {
        const selected = b.drone_id === selectedDroneId
        return (
          <div
            key={`${b.drone_id}-${b.color_index}`}
            className="dm-card"
            style={{ padding: '.5rem .65rem', borderLeft: `4px solid var(${laneToken(b.color_index)})`, outline: selected ? '1px solid var(--dm-text-on-dark)' : undefined, cursor: 'pointer' }}
            onClick={() => onSelect(selected ? null : b.drone_id)}
            data-testid="lane-card"
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '.5rem' }}>
              <strong style={{ fontSize: '.85rem' }}>{b.drone_name}</strong>
              <span className="dm-mono" style={{ fontSize: '.7rem' }} data-testid="lane-eta">
                ETA {formatDuration(b.eta_seconds)}
              </span>
            </div>
            <div className="dm-mono" style={{ fontSize: '.7rem', opacity: 0.8, margin: '.2rem 0 .35rem' }}>
              {b.task_count} TASKS · {formatDistance(b.distance_m)} · COV {formatPct(b.coverage_pct)}
            </div>
            <BatteryIndicator bundle={b} />
            {selected && (
              <div className="dm-mono" style={{ fontSize: '.65rem', opacity: 0.85, marginTop: '.35rem', lineHeight: 1.5 }}>
                {b.tasks.map((t) => `${t.task_key}@${formatDuration(t.eta_seconds)}`).join(' → ')}
              </div>
            )}
            {selected && onRemove && b.drone_id && (
              <button
                type="button"
                className="dm-btn dm-btn--danger"
                style={{ marginTop: '.4rem', width: '100%' }}
                disabled={busy}
                onClick={(e) => {
                  e.stopPropagation()
                  onRemove(b.drone_id!)
                }}
                data-testid="remove-drone"
              >
                Remove drone &amp; re-allocate
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}

/** Research/debug view of the coordination run, collapsed by default. */
export function CbbaDetailsPanel({ plan }: { plan: MissionPlan }) {
  const [open, setOpen] = useState(false)
  const sizes = plan.diagnostics.bundle_sizes ?? {}
  const history = (plan.diagnostics.history ?? []).filter((h) => h.iteration !== undefined)
  const realloc = (plan.diagnostics.history ?? []).find((h) => h.event === 'reallocation')
  return (
    <div className="dm-card">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{ background: 'transparent', border: 0, color: 'inherit', display: 'flex', alignItems: 'center', gap: '.4rem', padding: 0, textTransform: 'uppercase', fontSize: '.8rem', letterSpacing: '.06em' }}
        data-testid="cbba-details-toggle"
      >
        <ChevronRight size={14} style={{ transform: open ? 'rotate(90deg)' : undefined, transition: 'transform .15s' }} /> CBBA Details&hellip;
      </button>
      {open && (
        <div style={{ marginTop: '.5rem' }} data-testid="cbba-details">
          {(
            [
              ['Algorithm', plan.algorithm.toUpperCase()],
              ['Trigger', plan.trigger],
              ['Iterations', String(plan.iterations)],
              ['Consensus rounds', String(plan.consensus_rounds)],
              ['Messages exchanged', String(plan.messages_exchanged)],
              ['Convergence time', `${plan.convergence_time_ms.toFixed(1)} ms`],
              ['Communication', plan.diagnostics.communication ?? '—'],
              ['Tasks', String(plan.task_count)],
              ['Assigned / unassigned', `${plan.assigned_count} / ${plan.unassigned_count}`],
              ['Total distance', formatDistance(plan.total_distance_m)],
              ['Estimated energy', `${plan.estimated_energy_wh.toFixed(0)} Wh`],
              ['Average winning bid', plan.average_bid.toFixed(1)],
              ...(realloc ? [['Tasks moved (realloc)', String(realloc.tasks_moved ?? 0)]] : []),
            ] as [string, string][]
          ).map(([k, v]) => (
            <div key={k} className="dm-kv">
              <span>{k}</span>
              <span className="dm-mono">{v}</span>
            </div>
          ))}
          <div className="dm-card-title" style={{ marginTop: '.5rem', fontSize: '.7rem' }}>
            Bundle sizes
          </div>
          <div className="dm-mono" style={{ fontSize: '.7rem', opacity: 0.85 }}>
            {Object.entries(sizes)
              .map(([name, n]) => `${name}:${n}`)
              .join('  ')}
          </div>
          {plan.diagnostics.coverage?.passes.map((p) => (
            <div key={p.sensor} className="dm-mono" style={{ fontSize: '.7rem', opacity: 0.85, marginTop: '.25rem' }}>
              {p.sensor.toUpperCase()}: swath {p.swath_m} m · spacing {p.lane_spacing_m} m · bearing {p.lane_bearing_deg}° · {p.tasks} tasks
            </div>
          ))}
          {history.length > 0 && (
            <div className="dm-mono" style={{ fontSize: '.65rem', opacity: 0.75, marginTop: '.35rem' }}>
              Iteration trace: {history.map((h) => `#${h.iteration}:${h.assigned}${h.agree ? '✓' : ''}`).join(' ')}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

interface SummaryProps {
  wizard: WizardState
  plan: MissionPlan | null
  validation: ValidationReport | null
  realtimeConnected: boolean
  selectedDroneId: string | null
  onSelectLane: (droneId: string | null) => void
}

/** Right-hand summary: drones, tasks, battery, duration, coverage, validation. */
export function MissionSummaryPanel({ wizard, plan, validation, realtimeConnected, selectedDroneId, onSelectLane }: SummaryProps) {
  const droneIds = wizard.config.sensors?.drone_ids ?? []
  const minEnd = plan?.bundles.length ? Math.min(...plan.bundles.map((b) => b.battery_end_pct)) : null
  const warnings = plan?.bundles.filter((b) => b.battery_warning).length ?? 0
  const errorCount = validation?.checks.filter((c) => c.severity === 'error').length ?? 0
  return (
    <aside className="dm-wizard-panel dm-wizard-summary" aria-label="Mission summary" data-testid="mission-summary">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <StatusPill label={wizard.status} />
        <span className="dm-mono" style={{ fontSize: '.7rem', display: 'flex', alignItems: 'center', gap: '.3rem', opacity: 0.85 }} aria-live="polite">
          {realtimeConnected ? <Wifi size={14} /> : <WifiOff size={14} />} {realtimeConnected ? 'LIVE' : 'OFFLINE'}
        </span>
      </div>
      <div className="dm-card">
        <div className="dm-card-title">Mission Summary&hellip;</div>
        <div className="dm-kv">
          <span>Drones</span>
          <span className="dm-mono" data-testid="summary-drones">
            {plan ? `${plan.bundles.length} flying / ${plan.drone_count} eligible` : `${droneIds.length} selected`}
          </span>
        </div>
        <div className="dm-kv">
          <span>Tasks</span>
          <span className="dm-mono" data-testid="summary-tasks">
            {plan ? `${plan.assigned_count}/${plan.task_count}` : '--'}
          </span>
        </div>
        {plan && plan.unassigned_count > 0 && (
          <div className="dm-kv" style={{ color: '#FFB3AA' }}>
            <span>Unassigned</span>
            <span className="dm-mono">{plan.unassigned_count}</span>
          </div>
        )}
        <div className="dm-kv">
          <span>Est. duration</span>
          <span className="dm-mono" data-testid="summary-duration">
            {plan ? formatDuration(plan.estimated_duration_s) : '--'}
          </span>
        </div>
        <div className="dm-kv">
          <span>Total distance</span>
          <span className="dm-mono">{plan ? formatDistance(plan.total_distance_m) : '--'}</span>
        </div>
        <div className="dm-kv">
          <span>Energy</span>
          <span className="dm-mono">{plan ? `${plan.estimated_energy_wh.toFixed(0)} Wh` : '--'}</span>
        </div>
        <div className="dm-kv">
          <span>Coverage</span>
          <span className="dm-mono" data-testid="summary-coverage">
            {plan ? formatPct(plan.coverage_pct) : '--'}
          </span>
        </div>
        <div className="dm-kv">
          <span>Lowest landing SOC</span>
          <span className="dm-mono" style={{ color: warnings ? '#FFD08A' : undefined }}>
            {minEnd === null ? '--' : `${minEnd.toFixed(0)}%${warnings ? ` · ${warnings} ⚠` : ''}`}
          </span>
        </div>
        <div className="dm-kv">
          <span>Validation</span>
          <span className="dm-mono" data-testid="summary-validation">
            {!validation ? 'NOT RUN' : validation.passed ? 'PASSED' : `${errorCount} ERROR${errorCount === 1 ? '' : 'S'}`}
          </span>
        </div>
        {plan?.stale && <p className="dm-error">Parameters changed since this plan; re-run CBBA.</p>}
      </div>
      {plan && plan.bundles.length > 0 && (
        <div className="dm-card">
          <div className="dm-card-title">Lanes&hellip;</div>
          {plan.bundles.map((b) => (
            <button
              key={`${b.drone_id}-${b.color_index}`}
              type="button"
              onClick={() => onSelectLane(b.drone_id === selectedDroneId ? null : b.drone_id)}
              style={{ display: 'flex', alignItems: 'center', gap: '.5rem', width: '100%', background: 'transparent', border: 0, color: 'inherit', padding: '.2rem 0', fontSize: '.8rem', opacity: selectedDroneId && b.drone_id !== selectedDroneId ? 0.5 : 1 }}
            >
              <span style={{ width: 14, height: 4, background: `var(${laneToken(b.color_index)})`, borderRadius: 2 }} />
              <span style={{ flex: 1, textAlign: 'left' }}>{b.drone_name}</span>
              <span className="dm-mono">{formatDuration(b.eta_seconds)}</span>
            </button>
          ))}
        </div>
      )}
      <div style={{ marginTop: 'auto', fontSize: '.7rem', opacity: 0.5, textTransform: 'uppercase' }}>
        Live Tiles
        <br />
        <span className="dm-mono">Operation: {wizard.name}</span>
      </div>
    </aside>
  )
}
