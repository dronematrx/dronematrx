import { useEffect, useState } from 'react'
import { AlertTriangle, GitCompare, RotateCcw } from 'lucide-react'
import * as simApi from '../../api/simulations'
import type { ReportCandidate, ReportComparison, SimReport } from '../../api/simulations'
import { formatDistance, formatDuration, formatPct } from '../../lib/format'

const DIFF_LABELS: Record<string, string> = {
  coverage_pct: 'Coverage %',
  battery_used_total_pct: 'Battery used (total %)',
  battery_used_mean_pct: 'Battery used (mean %)',
  simulated_duration_s: 'Mission duration (sim s)',
  tasks_completed: 'Completed tasks',
  tasks_failed: 'Failed tasks',
  drone_failures: 'Drone failures',
}
// Higher is better for these; lower is better for the rest.
const HIGHER_BETTER = new Set(['coverage_pct', 'tasks_completed'])

interface Props {
  sessionId: string
  canWrite: boolean
  onRerun: () => void
}

export function SimReportView({ sessionId, canWrite, onRerun }: Props) {
  const [report, setReport] = useState<SimReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<ReportCandidate[]>([])
  const [baseline, setBaseline] = useState('')
  const [comparison, setComparison] = useState<ReportComparison | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      for (let i = 0; i < 10 && !cancelled; i++) {
        try {
          const r = await simApi.getSimReport(sessionId)
          if (!cancelled) setReport(r)
          break
        } catch (err) {
          if (i === 9 && !cancelled) setError(err instanceof Error ? err.message : 'Report unavailable')
          await new Promise((res) => setTimeout(res, 1500))
        }
      }
      simApi.getReportCandidates(sessionId).then((c) => !cancelled && setCandidates(c)).catch(() => undefined)
    }
    load()
    return () => {
      cancelled = true
    }
  }, [sessionId])

  if (error) return <p className="dm-error">{error}</p>
  if (!report) return <p className="dm-mono" style={{ fontSize: '.8rem' }}>Generating report…</p>
  const d = report.data
  const ms = d.mission_summary
  const ts = d.task_summary

  const compare = async () => {
    if (!baseline) return
    setComparison(await simApi.compareReports(sessionId, baseline))
  }

  return (
    <div style={{ display: 'grid', gap: 'var(--dm-space-sm)' }} data-testid="sim-report">
      <div className="dm-card" style={{ background: 'var(--dm-navy-900)' }}>
        <div className="dm-card-title" style={{ fontSize: '.85rem' }}>Simulation report · {ms.mission_name}</div>
        <div className="dm-stat-grid" style={{ marginBottom: 0 }}>
          <div className="dm-stat"><span>Status</span><strong data-testid="report-status">{ms.status.toUpperCase()}</strong></div>
          <div className="dm-stat"><span>Mission</span><strong>{ms.mission_state.toUpperCase()}</strong></div>
          <div className="dm-stat"><span>Task type</span><strong>{ms.task_type}</strong></div>
          <div className="dm-stat"><span>Drones req / spawned</span><strong data-testid="report-drones">{ms.drones_requested} / {ms.drones_spawned}</strong></div>
          <div className="dm-stat"><span>Sim duration</span><strong>{formatDuration(ms.simulated_duration_s)}</strong></div>
          <div className="dm-stat"><span>Wall duration</span><strong>{formatDuration(ms.wall_duration_s)}</strong></div>
          <div className="dm-stat"><span>Tasks done</span><strong data-testid="report-tasks">{ts.completed}/{ts.assigned}</strong></div>
          <div className="dm-stat"><span>Failed / unassigned</span><strong>{ts.failed} / {ts.unassigned}</strong></div>
          <div className="dm-stat"><span>Coverage</span><strong data-testid="report-coverage">{formatPct(ts.coverage_pct, 1)}</strong></div>
          <div className="dm-stat"><span>Battery used</span><strong>{formatPct(d.battery.total_used_pct, 1)}</strong></div>
          <div className="dm-stat"><span>Energy (est.)</span><strong>{d.battery.energy_wh_estimate ?? '--'} Wh</strong></div>
          <div className="dm-stat"><span>Report generated</span><strong data-testid="report-generation-ms">{report.generation_ms.toFixed(0)} ms</strong></div>
        </div>
        {!d.data_completeness.complete && (
          <div className="dm-banner dm-banner--amber"><AlertTriangle size={14} /> Missing telemetry for {d.data_completeness.missing_telemetry.join(', ')}</div>
        )}
      </div>

      <div style={{ overflowX: 'auto' }}>
      <table className="dm-data-table" data-testid="report-flight-table" style={{ fontSize: '.72rem' }}>
        <thead><tr><th>Drone</th><th>State</th><th>Distance</th><th>Flight</th><th>Battery</th><th>Used</th><th>WPs</th><th>Tasks</th><th>Interrupted</th><th>GPS err</th></tr></thead>
        <tbody>
          {d.flight_summary.map((f) => (
            <tr key={f.uin}>
              <td>{f.name}</td>
              <td className="dm-mono">{f.final_state}</td>
              <td className="dm-mono">{formatDistance(f.distance_m)}</td>
              <td className="dm-mono">{formatDuration(f.flight_duration_s)}</td>
              <td className="dm-mono">{f.battery_start_pct ?? '--'}→{f.battery_end_pct ?? '--'}%</td>
              <td className="dm-mono">{f.battery_used_pct ?? '--'}%</td>
              <td className="dm-mono">{f.waypoints_reached ?? '--'}</td>
              <td className="dm-mono">{f.tasks_completed}</td>
              <td className="dm-mono">{f.tasks_interrupted.length}</td>
              <td className="dm-mono">{f.gps_error_max_m ?? '--'} m</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 'var(--dm-space-sm)' }}>
        <div className="dm-card" style={{ background: 'var(--dm-navy-900)' }} data-testid="report-failures">
          <div className="dm-card-title" style={{ fontSize: '.8rem' }}>Failure summary</div>
          {Object.entries(d.failure_summary).map(([k, v]) => (
            <div key={k} className="dm-kv"><span>{k.replaceAll('_', ' ')}</span><strong className="dm-mono">{v}</strong></div>
          ))}
        </div>
        <div className="dm-card" style={{ background: 'var(--dm-navy-900)' }} data-testid="report-environment">
          <div className="dm-card-title" style={{ fontSize: '.8rem' }}>Simulation environment</div>
          <div className="dm-kv"><span>Gazebo</span><strong className="dm-mono">{d.environment.gazebo ?? '--'}</strong></div>
          <div className="dm-kv"><span>SITL</span><strong className="dm-mono">PX4 {d.environment.sitl ?? '--'}</strong></div>
          <div className="dm-kv"><span>Terrain</span><strong className="dm-mono" style={{ fontSize: '.68rem' }}>{d.environment.terrain_fallback ? 'FALLBACK flat' : d.environment.dem_source ?? '--'}</strong></div>
          <div className="dm-kv"><span>Resolution</span><strong className="dm-mono">{d.environment.terrain_resolution_m ?? '--'} m</strong></div>
          <div className="dm-kv"><span>Speed · active drones</span><strong className="dm-mono">{d.environment.simulation_speed}× · {d.environment.active_drones}</strong></div>
          <div className="dm-kv"><span>World gen · SITL startup</span><strong className="dm-mono">{d.environment.world_generation_s ?? '--'} s · {d.environment.sitl_startup_s ?? '--'} s</strong></div>
        </div>
      </div>

      <div className="dm-card" style={{ background: 'var(--dm-navy-900)', display: 'grid', gap: '.5rem' }} data-testid="report-compare">
        <div className="dm-card-title" style={{ fontSize: '.8rem' }}>Compare with a previous report</div>
        <div style={{ display: 'flex', gap: '.4rem', flexWrap: 'wrap' }}>
          <select className="dm-input" value={baseline} onChange={(e) => setBaseline(e.target.value)} data-testid="report-baseline">
            <option value="">Select a previous simulation…</option>
            {candidates.map((c) => (
              <option key={c.session_id} value={c.session_id}>{c.mission_name} · {c.task_type} · {c.drones} drones · {c.status}</option>
            ))}
          </select>
          <button type="button" className="dm-btn dm-btn--primary" disabled={!baseline} onClick={compare} data-testid="report-compare-btn"><GitCompare size={13} /> Compare</button>
          {canWrite && <button type="button" className="dm-btn" onClick={onRerun} data-testid="report-rerun"><RotateCcw size={13} /> Re-run (comparable)</button>}
        </div>
        {comparison && (
          <>
            {comparison.warnings.length > 0 && (
              <div className={`dm-banner ${comparison.compatible ? 'dm-banner--amber' : 'dm-banner--bad'}`} data-testid="report-compare-warning">
                <AlertTriangle size={14} /> {comparison.compatible ? 'Comparable, but:' : 'Not directly comparable:'} {comparison.warnings.join('; ')}
              </div>
            )}
            <table className="dm-data-table" data-testid="report-diff" style={{ tableLayout: 'fixed', width: '100%', fontSize: '.72rem' }}>
              <colgroup><col style={{ width: '40%' }} /><col style={{ width: '20%' }} /><col style={{ width: '20%' }} /><col style={{ width: '20%' }} /></colgroup>
              <thead><tr><th>Metric</th><th>Now</th><th>Base</th><th>Δ</th></tr></thead>
              <tbody>
                {Object.entries(comparison.diff).map(([k, v]) => {
                  const better = v.delta === null || v.delta === 0 ? null : HIGHER_BETTER.has(k) ? v.delta > 0 : v.delta < 0
                  return (
                    <tr key={k}>
                      <td>{DIFF_LABELS[k] ?? k}</td>
                      <td className="dm-mono">{v.current ?? '--'}</td>
                      <td className="dm-mono">{v.baseline ?? '--'}</td>
                      <td className="dm-mono" style={{ fontWeight: 700, color: better === null ? undefined : better ? '#5CC28A' : '#FF8A80' }}>
                        {v.delta === null ? '--' : `${v.delta > 0 ? '+' : ''}${v.delta}`}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  )
}
