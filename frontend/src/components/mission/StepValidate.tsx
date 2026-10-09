import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Play, RefreshCw, ShieldCheck } from 'lucide-react'
import type { Drone, MissionPlan, MissionStatus, ReadinessResult, ValidationCheck, ValidationReport, WizardState } from '../../api/types'
import type { ActionError } from '../../store/useMissionStore'
import { formatDuration } from '../../lib/format'
import { CbbaDetailsPanel, LaneList } from './MissionPanels'
import { ConfirmDialog, StepHeading, StepNav } from './WizardControls'

const SEVERITY_COLOR: Record<ValidationCheck['severity'], string> = {
  error: 'var(--dm-geo-nfz)',
  warning: 'var(--dm-status-armed)',
  info: 'var(--dm-status-active)',
}

export function CheckList({ checks }: { checks: ValidationCheck[] }) {
  const order = { error: 0, warning: 1, info: 2 }
  return (
    <div data-testid="validation-checks">
      {[...checks]
        .sort((a, b) => order[a.severity] - order[b.severity])
        .map((c, i) => (
          <div key={`${c.code}-${i}`} className="dm-check" data-testid={`check-${c.code}`}>
            <span style={{ width: 8, height: 8, marginTop: 5, borderRadius: '50%', background: SEVERITY_COLOR[c.severity], flexShrink: 0 }} aria-hidden />
            <span>
              <strong className="dm-mono" style={{ fontSize: '.7rem' }}>
                {c.severity.toUpperCase()} · {c.code}
              </strong>
              <br />
              {c.message}
            </span>
          </div>
        ))}
    </div>
  )
}

type PendingAction = { target: MissionStatus; title: string; confirm: string; tone: 'primary' | 'danger' | 'armed'; message: string } | null

interface StepValidateProps {
  wizard: WizardState
  plan: MissionPlan | null
  validation: ValidationReport | null
  readiness: ReadinessResult | null
  error: ActionError | null
  busy: string | null
  readOnly: boolean
  drones: Drone[]
  selectedDroneId: string | null
  onSelectLane: (droneId: string | null) => void
  onBack: () => void
  onRunCbba: () => void
  onValidate: () => void
  onTransition: (target: MissionStatus, reason?: string) => void
  onAddDrone: (droneId: string) => void
  onRemoveDrone: (droneId: string) => void
  onLoadReadiness: () => void
}

export function StepValidate(props: StepValidateProps) {
  const { wizard, plan, validation, readiness, error, busy, readOnly, drones, onLoadReadiness } = props
  const [pending, setPending] = useState<PendingAction>(null)
  const [addId, setAddId] = useState('')
  const status = wizard.status
  const editable = wizard.editable && !readOnly
  const roster = wizard.config.sensors?.drone_ids ?? []
  const addable = drones.filter((d) => d.active && !roster.includes(d.id))

  useEffect(() => {
    if (status === 'armed') onLoadReadiness()
  }, [status, onLoadReadiness])

  const validationFresh = validation && plan && validation.plan_id === plan.id && !plan.stale
  const canArm = !readOnly && status === 'validating' && !!validationFresh && validation!.passed
  const nfzConflict = !!validation?.nfz_conflict
  const lifecycleErrors = error?.checks ?? []

  return (
    <>
      <StepHeading title="Step 6 · Validate & Allocate">Runs CBBA on the edge server, then PostGIS safety checks (NFZ, geofences) and feasibility checks.</StepHeading>

      {editable && (
        <div style={{ display: 'flex', gap: 'var(--dm-space-sm)', flexWrap: 'wrap' }}>
          <button type="button" className="dm-btn" onClick={props.onRunCbba} disabled={!!busy} data-testid="run-cbba">
            <RefreshCw size={14} /> {busy === 'allocating' ? 'Allocating…' : 'Run CBBA'}
          </button>
          <button type="button" className="dm-btn dm-btn--primary" onClick={props.onValidate} disabled={!!busy} data-testid="validate-mission">
            <ShieldCheck size={14} /> {busy === 'validating' ? 'Validating…' : 'Validate'}
          </button>
        </div>
      )}

      {plan ? (
        plan.converged ? (
          <div className="dm-banner dm-banner--ok" data-testid="cbba-status">
            <CheckCircle2 size={18} /> CBBA Converged
            <span className="dm-mono" style={{ fontWeight: 400, fontSize: '.75rem', marginLeft: 'auto' }}>
              {plan.iterations} IT · {plan.convergence_time_ms.toFixed(0)} MS
            </span>
          </div>
        ) : (
          <div className="dm-banner dm-banner--bad" data-testid="cbba-status">
            <AlertTriangle size={18} /> CBBA Failed to Converge
            <span className="dm-mono" style={{ fontWeight: 400, fontSize: '.75rem', marginLeft: 'auto' }}>
              {plan.timed_out ? 'TIMEOUT' : `${plan.iterations} IT`}
            </span>
          </div>
        )
      ) : (
        <p style={{ fontSize: '.8rem', opacity: 0.75 }}>No allocation yet. Run CBBA or Validate.</p>
      )}

      {nfzConflict && (
        <div className="dm-banner dm-banner--nfz" role="alert" data-testid="nfz-conflict-banner">
          <AlertTriangle size={18} /> NFZ Conflict · Launch Blocked
        </div>
      )}

      {plan && (
        <div className="dm-card" data-testid="cbba-summary">
          <div className="dm-kv">
            <span>Drones / tasks</span>
            <span className="dm-mono">
              {plan.bundles.length} / {plan.task_count}
            </span>
          </div>
          <div className="dm-kv">
            <span>Assigned · unassigned</span>
            <span className="dm-mono">
              {plan.assigned_count} · {plan.unassigned_count}
            </span>
          </div>
          <div className="dm-kv">
            <span>Mission duration</span>
            <span className="dm-mono">{formatDuration(plan.estimated_duration_s)}</span>
          </div>
          <div className="dm-kv">
            <span>Coverage</span>
            <span className="dm-mono" data-testid="plan-coverage">
              {plan.coverage_pct.toFixed(0)}% (max {plan.max_coverage_pct.toFixed(0)}%)
            </span>
          </div>
        </div>
      )}

      {validation && (
        <div className="dm-card">
          <div className="dm-card-title" data-testid="validation-result">
            Validation&hellip; {validation.passed ? 'PASSED' : 'FAILED'}
            {plan && validation.plan_id !== plan.id && ' (outdated)'}
          </div>
          <CheckList checks={validation.checks} />
        </div>
      )}

      {lifecycleErrors.length > 0 && (
        <div className="dm-card" style={{ border: '1px solid var(--dm-geo-nfz)' }}>
          <div className="dm-card-title">Blocked&hellip;</div>
          <CheckList checks={lifecycleErrors} />
        </div>
      )}

      {plan && (
        <div className="dm-card">
          <div className="dm-card-title">Drone Lanes&hellip;</div>
          <LaneList bundles={plan.bundles} selectedDroneId={props.selectedDroneId} onSelect={props.onSelectLane} onRemove={editable ? props.onRemoveDrone : undefined} busy={!!busy} />
          {editable && addable.length > 0 && (
            <div style={{ display: 'flex', gap: '.4rem', marginTop: '.5rem' }}>
              <select className="dm-input" style={{ flex: 1 }} value={addId} onChange={(e) => setAddId(e.target.value)} aria-label="Drone to add" data-testid="add-drone-select">
                <option value="">Add drone…</option>
                {addable.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} ({d.battery.toFixed(0)}%)
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="dm-btn"
                disabled={!addId || !!busy}
                onClick={() => {
                  props.onAddDrone(addId)
                  setAddId('')
                }}
                data-testid="add-drone"
              >
                Add &amp; re-allocate
              </button>
            </div>
          )}
        </div>
      )}

      {plan && <CbbaDetailsPanel plan={plan} />}

      {status === 'armed' && readiness && (
        <div className="dm-card">
          <div className="dm-card-title">Launch readiness&hellip; {readiness.ready ? 'READY' : 'NOT READY'}</div>
          {readiness.checks.length ? <CheckList checks={readiness.checks} /> : <p style={{ fontSize: '.8rem', margin: 0 }}>All assigned drones are ready.</p>}
        </div>
      )}

      {!readOnly && (
        <div style={{ display: 'flex', gap: 'var(--dm-space-sm)', flexWrap: 'wrap' }} aria-label="Mission lifecycle actions">
          {(status === 'validating' || status === 'planning') && (
            <button
              type="button"
              className="dm-btn dm-btn--armed"
              disabled={!canArm || !!busy}
              title={nfzConflict ? 'NFZ conflict must be resolved before arming' : !validationFresh ? 'Validate the current plan first' : undefined}
              onClick={() => setPending({ target: 'armed', title: 'Arm mission?', confirm: 'Arm', tone: 'armed', message: `Arm "${wizard.name}" with ${plan?.bundles.length ?? 0} drones? The server re-runs every safety check first.` })}
              data-testid="arm-mission"
            >
              Arm
            </button>
          )}
          {status === 'armed' && (
            <>
              <button
                type="button"
                className="dm-btn dm-btn--primary"
                disabled={!!busy}
                onClick={() => setPending({ target: 'active', title: 'Launch mission?', confirm: 'Activate', tone: 'primary', message: 'Start the mission. Drones will take off on their CBBA bundles.' })}
                data-testid="activate-mission"
              >
                <Play size={14} /> Activate
              </button>
              <button type="button" className="dm-btn" disabled={!!busy} onClick={() => props.onTransition('validating', 'Disarmed by operator')} data-testid="disarm-mission">
                Disarm
              </button>
            </>
          )}
          {status === 'active' && (
            <button type="button" className="dm-btn dm-btn--primary" disabled={!!busy} onClick={() => props.onTransition('completed', 'Completed by operator')} data-testid="complete-mission">
              Complete
            </button>
          )}
          {!['completed', 'aborted'].includes(status) && (
            <button
              type="button"
              className="dm-btn dm-btn--danger"
              style={{ marginLeft: 'auto' }}
              disabled={!!busy}
              onClick={() => setPending({ target: 'aborted', title: 'Abort mission?', confirm: 'Abort', tone: 'danger', message: 'Aborting is final: the mission cannot be resumed.' })}
              data-testid="abort-mission"
            >
              Abort
            </button>
          )}
        </div>
      )}

      <StepNav step={6} canGoBack={wizard.editable} onBack={props.onBack} readOnly />

      {pending && (
        <ConfirmDialog
          title={pending.title}
          message={pending.message}
          confirmLabel={pending.confirm}
          tone={pending.tone}
          onCancel={() => setPending(null)}
          onConfirm={() => {
            props.onTransition(pending.target)
            setPending(null)
          }}
        />
      )}
    </>
  )
}
