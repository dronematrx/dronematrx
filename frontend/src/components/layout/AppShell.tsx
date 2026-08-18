import { useState, type ReactNode } from 'react'
import { TopNavBar } from './TopNavBar'
import { SidebarNav } from './SidebarNav'
import { PageTitleBar } from './PageTitleBar'

interface AppShellProps {
  title: string
  actions?: ReactNode
  children: ReactNode
  /** Map/telemetry views render full-bleed content beneath the title bar with no outer padding. */
  fullBleed?: boolean
}

export function AppShell({ title, actions, children, fullBleed = false }: AppShellProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <div className="app-shell" style={{ height: '100vh', background: 'var(--dm-charcoal-950)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <TopNavBar onToggleSidebar={() => setSidebarOpen(true)} />
      <SidebarNav open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
      <PageTitleBar title={title} actions={actions} />
      <main style={{ flex: '1 1 auto', padding: fullBleed ? 0 : 'var(--dm-space-lg)', minHeight: 0, display: 'flex', flexDirection: 'column', overflow: fullBleed ? 'hidden' : 'auto' }}>
        {children}
      </main>
    </div>
  )
}
