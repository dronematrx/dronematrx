import { ChevronRight, X } from 'lucide-react'
import { useNavigate, useLocation } from 'react-router-dom'

const NAV_ITEMS: { label: string; path: string }[] = [
  { label: 'Mission', path: '/missions' },
  { label: 'Flight Plan', path: '/map' },
  { label: 'Analyse', path: '/analytics' },
  { label: 'Fleet', path: '/fleet' },
  { label: 'Reports', path: '/reports' },
  { label: 'Settings', path: '/settings' },
]

interface SidebarNavProps {
  open: boolean
  onClose: () => void
}

export function SidebarNav({ open, onClose }: SidebarNavProps) {
  const navigate = useNavigate()
  const location = useLocation()

  return (
    <>
      <aside
        aria-hidden={!open}
        style={{
          position: 'fixed',
          zIndex: 40,
          inset: '0 auto 0 0',
          width: 'min(22rem, 82vw)',
          padding: 'var(--dm-space-lg)',
          transform: open ? 'translateX(0)' : 'translateX(-100%)',
          transition: 'transform .25s ease',
          color: 'var(--dm-text-on-dark)',
          background: 'color-mix(in srgb, var(--dm-charcoal-950) 94%, transparent)',
          boxShadow: '.6rem 0 1.5rem rgb(0 0 0 / .3)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '2.5rem', textTransform: 'uppercase', letterSpacing: '.1em' }}>
          <span>Drone Matrx</span>
          <button type="button" aria-label="Close navigation" onClick={onClose} style={{ background: 'transparent', border: 0, color: 'inherit' }}>
            <X />
          </button>
        </div>
        <nav aria-label="Primary navigation" style={{ display: 'grid', gap: '.35rem' }}>
          {NAV_ITEMS.map((item) => {
            const active = location.pathname === item.path
            return (
              <button
                key={item.label}
                type="button"
                onClick={() => {
                  navigate(item.path)
                  onClose()
                }}
                style={{
                  border: 0,
                  color: 'var(--dm-text-on-dark)',
                  background: active ? 'color-mix(in srgb, var(--dm-slate-700) 55%, transparent)' : 'transparent',
                  opacity: active ? 1 : 0.75,
                  letterSpacing: '.07em',
                  textTransform: 'uppercase',
                  padding: '1rem',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '.6rem',
                  textAlign: 'left',
                  fontSize: '1.1rem',
                }}
              >
                <ChevronRight size={18} /> {item.label}
              </button>
            )
          })}
        </nav>
      </aside>
      {open && (
        <button
          type="button"
          aria-label="Dismiss navigation"
          onClick={onClose}
          style={{ position: 'fixed', inset: 0, zIndex: 35, border: 0, background: 'rgb(0 0 0 / .35)' }}
        />
      )}
    </>
  )
}
