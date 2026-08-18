import { useEffect, useState } from 'react'
import { LogOut, Menu, Search } from 'lucide-react'
import { useAuthStore } from '../../store/useAuthStore'

interface TopNavBarProps {
  onToggleSidebar: () => void
}

export function TopNavBar({ onToggleSidebar }: TopNavBarProps) {
  const [clock, setClock] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)

  useEffect(() => {
    const update = () =>
      setClock(
        new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
          .format(new Date())
          .replace(/:/g, ' : '),
      )
    update()
    const timer = window.setInterval(update, 1000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <header
      className="dm-topnav"
      style={{
        height: '5rem',
        background: 'var(--dm-navy-900)',
        color: 'var(--dm-text-on-dark)',
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--dm-space-lg)',
        padding: `0 var(--dm-space-lg)`,
        flexShrink: 0,
        zIndex: 30,
      }}
    >
      <button
        type="button"
        aria-label="Open navigation"
        onClick={onToggleSidebar}
        style={{ background: 'transparent', border: 0, color: 'inherit', display: 'grid', placeItems: 'center' }}
      >
        <Menu size={28} />
      </button>

      <div style={{ display: 'flex', alignItems: 'center', gap: '.75rem', textTransform: 'uppercase', letterSpacing: '.1em', fontSize: '1.15rem' }}>
        DRONE MATRX
      </div>

      <nav aria-label="Page actions" style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '1.75rem', textTransform: 'uppercase', fontSize: '.9rem', letterSpacing: '.05em' }}>
        <button
          type="button"
          aria-label="Open search"
          onClick={() => setSearchOpen((v) => !v)}
          style={{ background: 'transparent', border: 0, color: 'inherit', display: 'flex', alignItems: 'center', gap: '.5rem' }}
        >
          <span style={{ width: '2rem', height: '2rem', borderRadius: 'var(--dm-radius-circle)', background: 'var(--dm-text-on-dark)', color: 'var(--dm-navy-900)', display: 'grid', placeItems: 'center' }}>
            <Search size={16} />
          </span>
          + Search
        </button>

        {user && (
          <span className="dm-mono" style={{ fontSize: '.8rem', opacity: 0.85 }}>
            {user.full_name} &middot; {user.role}
          </span>
        )}

        <button
          type="button"
          aria-label="Log out"
          onClick={logout}
          style={{ background: 'transparent', border: 0, color: 'inherit', display: 'flex', alignItems: 'center', gap: '.4rem' }}
        >
          <LogOut size={16} /> Logout
        </button>

        <button
          type="button"
          style={{ background: 'transparent', border: 0, color: 'var(--dm-status-alert)', fontWeight: 700, letterSpacing: '.08em' }}
        >
          ALERT
        </button>

        <time className="dm-mono" aria-live="polite" style={{ whiteSpace: 'nowrap', fontSize: '.9rem' }}>
          {clock}
        </time>
      </nav>

      {searchOpen && (
        <form
          onSubmit={(e) => e.preventDefault()}
          style={{
            position: 'absolute',
            right: 'var(--dm-space-lg)',
            top: '100%',
            background: 'var(--dm-navy-900)',
            padding: 'var(--dm-space-sm)',
            boxShadow: '0 .75rem 1.5rem rgb(0 0 0 / .35)',
            zIndex: 31,
          }}
        >
          <label htmlFor="global-search" style={{ position: 'absolute', width: 1, height: 1, overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>
            Search Drone Matrx
          </label>
          <input
            id="global-search"
            autoFocus
            placeholder="Search fleet, missions..."
            style={{ border: 0, minWidth: '18rem', padding: '.7rem 1rem', background: 'var(--dm-surface-inset)', color: 'var(--dm-text-on-dark)', borderRadius: 'var(--dm-radius-sm)' }}
          />
        </form>
      )}
    </header>
  )
}
