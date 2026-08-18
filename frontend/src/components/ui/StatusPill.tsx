type Tone = 'active' | 'pending' | 'alert' | 'offline' | 'info'

const TONE_COLOR: Record<Tone, string> = {
  active: 'var(--dm-status-active)',
  pending: 'var(--dm-status-pending)',
  alert: 'var(--dm-status-alert)',
  offline: 'var(--dm-status-offline)',
  info: 'var(--dm-status-info)',
}

const STATUS_TONE: Record<string, Tone> = {
  online: 'active',
  active: 'active',
  mission: 'active',
  completed: 'active',
  warning: 'pending',
  draft: 'pending',
  maintenance: 'pending',
  calibrated: 'active',
  uncalibrated: 'pending',
  due: 'pending',
  critical: 'alert',
  fault: 'alert',
  aborted: 'alert',
  offline: 'offline',
}

interface StatusPillProps {
  label: string
  tone?: Tone
}

export function StatusPill({ label, tone }: StatusPillProps) {
  const resolvedTone = tone ?? STATUS_TONE[label.toLowerCase()] ?? 'info'
  return (
    <span
      style={{
        display: 'inline-block',
        borderRadius: 'var(--dm-radius-pill)',
        padding: '.3rem .9rem',
        fontSize: '.75rem',
        fontWeight: 700,
        letterSpacing: '.05em',
        textTransform: 'uppercase',
        background: TONE_COLOR[resolvedTone],
        color: 'var(--dm-text-on-dark)',
        whiteSpace: 'nowrap',
      }}
    >
      {label}
    </span>
  )
}
