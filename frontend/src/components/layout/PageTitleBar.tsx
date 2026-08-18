interface PageTitleBarProps {
  title: string
  actions?: React.ReactNode
}

export function PageTitleBar({ title, actions }: PageTitleBarProps) {
  return (
    <div
      style={{
        background: 'var(--dm-slate-700)',
        color: 'var(--dm-text-on-dark)',
        padding: '1rem var(--dm-space-lg)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        textTransform: 'uppercase',
        letterSpacing: '.08em',
        fontSize: '1.5rem',
        fontWeight: 700,
        flexShrink: 0,
      }}
    >
      <span>{title}&hellip;</span>
      {actions && <div style={{ display: 'flex', gap: '.75rem', textTransform: 'none', letterSpacing: 'normal' }}>{actions}</div>}
    </div>
  )
}
