import { AppShell } from '../components/layout/AppShell'

export function StubPage({ title }: { title: string }) {
  return (
    <AppShell title={title}>
      <div style={{ background: 'var(--dm-navy-900)', borderRadius: 'var(--dm-radius-md)', padding: 'var(--dm-space-lg)', textAlign: 'center', opacity: 0.7, color: 'var(--dm-text-on-dark)' }}>
        {title} is coming soon.
      </div>
    </AppShell>
  )
}
