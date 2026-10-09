import { Check, ChevronLeft, ChevronRight, Save } from 'lucide-react'
import type { ReactNode } from 'react'
import { STEP_LABELS } from '../../lib/wizardValidation'

interface StepperProps {
  current: number
  completed: number[]
  furthest: number
  onSelect: (step: number) => void
}

/** Progress stepper: labels, checkmarks for completed steps (PRD wizard spec). */
export function WizardStepper({ current, completed, furthest, onSelect }: StepperProps) {
  return (
    <nav className="dm-stepper" aria-label="Mission planning steps">
      {STEP_LABELS.map((label, idx) => {
        const step = idx + 1
        const done = completed.includes(step)
        const reachable = step <= furthest
        return (
          <button
            key={label}
            type="button"
            className={`dm-step${done ? ' dm-step--done' : ''}`}
            aria-current={step === current ? 'step' : undefined}
            disabled={!reachable}
            onClick={() => onSelect(step)}
            data-testid={`stepper-${step}`}
          >
            <span className="dm-step-num">{done ? <Check size={14} /> : step}</span>
            {label}
          </button>
        )
      })}
    </nav>
  )
}

interface StepNavProps {
  step: number
  canGoBack: boolean
  onBack: () => void
  onSaveExit?: () => void
  onNext?: () => void
  nextLabel?: string
  busy?: boolean
  readOnly?: boolean
}

export function StepNav({ step, canGoBack, onBack, onSaveExit, onNext, nextLabel = 'Next', busy = false, readOnly = false }: StepNavProps) {
  return (
    <div style={{ display: 'flex', gap: 'var(--dm-space-sm)', marginTop: 'auto', paddingTop: 'var(--dm-space-md)', flexWrap: 'wrap' }}>
      <button type="button" className="dm-btn dm-btn--ghost" onClick={onBack} disabled={!canGoBack || busy} data-testid="wizard-back">
        <ChevronLeft size={14} /> Back
      </button>
      {!readOnly && onSaveExit && (
        <button type="button" className="dm-btn" onClick={onSaveExit} disabled={busy} data-testid="wizard-save-exit">
          <Save size={14} /> Save &amp; Exit
        </button>
      )}
      {!readOnly && onNext && (
        <button type="button" className="dm-btn dm-btn--primary" style={{ marginLeft: 'auto' }} onClick={onNext} disabled={busy} data-testid={`wizard-next-${step}`}>
          {busy ? 'Saving…' : nextLabel} <ChevronRight size={14} />
        </button>
      )}
    </div>
  )
}

export function FieldError({ message }: { message?: string }) {
  if (!message) return null
  return (
    <span className="dm-error" role="alert">
      {message}
    </span>
  )
}

export function StepHeading({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div>
      <h2 style={{ margin: 0, fontSize: '1.25rem', textTransform: 'uppercase', letterSpacing: '.06em' }}>{title}&hellip;</h2>
      {children && <p style={{ margin: '.35rem 0 0', fontSize: '.8rem', opacity: 0.75 }}>{children}</p>}
    </div>
  )
}

interface ConfirmDialogProps {
  title: string
  message: ReactNode
  confirmLabel: string
  tone?: 'primary' | 'danger' | 'armed'
  onConfirm: () => void
  onCancel: () => void
}

/** Spec 5.8: centered navy dialog. Used for ARM / ACTIVATE / ABORT. */
export function ConfirmDialog({ title, message, confirmLabel, tone = 'primary', onConfirm, onCancel }: ConfirmDialogProps) {
  return (
    <div role="dialog" aria-modal="true" aria-label={title} style={{ position: 'fixed', inset: 0, background: 'rgb(0 0 0 / .55)', display: 'grid', placeItems: 'center', zIndex: 2000 }}>
      <div style={{ background: 'var(--dm-navy-900)', borderRadius: 'var(--dm-radius-md)', padding: 'var(--dm-space-lg)', width: 'min(26rem, 92vw)', display: 'grid', gap: 'var(--dm-space-md)' }}>
        <h2 style={{ margin: 0, textTransform: 'uppercase', letterSpacing: '.06em', fontSize: '1.25rem' }}>{title}</h2>
        <div style={{ fontSize: '.9rem' }}>{message}</div>
        <div style={{ display: 'flex', gap: 'var(--dm-space-sm)', justifyContent: 'flex-end' }}>
          <button type="button" className="dm-btn dm-btn--ghost" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className={`dm-btn dm-btn--${tone}`} onClick={onConfirm} data-testid="confirm-dialog-ok">
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
