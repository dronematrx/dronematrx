import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '../store/useAuthStore'

export function LoginPage() {
  const login = useAuthStore((s) => s.login)
  const status = useAuthStore((s) => s.status)
  const navigate = useNavigate()
  const [email, setEmail] = useState('admin@dronematrx.com')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      await login(email, password)
      navigate('/fleet')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    }
  }

  return (
    <div
      className="app-shell"
      style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', background: 'var(--dm-charcoal-950)', color: 'var(--dm-text-on-dark)' }}
    >
      <form
        onSubmit={submit}
        style={{ background: 'var(--dm-navy-900)', borderRadius: 'var(--dm-radius-md)', padding: 'var(--dm-space-lg)', width: 'min(24rem, 90vw)', display: 'grid', gap: 'var(--dm-space-md)' }}
      >
        <h1 style={{ margin: 0, fontSize: '1.5rem', textTransform: 'uppercase', letterSpacing: '.08em' }}>Drone Matrx</h1>
        <p style={{ margin: 0, opacity: 0.7, fontSize: '.9rem' }}>Sign in to mission control</p>

        <label style={{ display: 'grid', gap: '.3rem', fontSize: '.85rem' }}>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            style={{ border: 0, borderRadius: 'var(--dm-radius-sm)', padding: '.65rem .8rem', background: 'var(--dm-surface-inset)', color: 'var(--dm-text-on-dark)' }}
          />
        </label>

        <label style={{ display: 'grid', gap: '.3rem', fontSize: '.85rem' }}>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            style={{ border: 0, borderRadius: 'var(--dm-radius-sm)', padding: '.65rem .8rem', background: 'var(--dm-surface-inset)', color: 'var(--dm-text-on-dark)' }}
          />
        </label>

        {error && <p style={{ color: 'var(--dm-status-alert)', fontSize: '.85rem', margin: 0 }}>{error}</p>}

        <button
          type="submit"
          disabled={status === 'loading'}
          style={{ background: 'var(--dm-status-active)', color: 'white', border: 0, borderRadius: 'var(--dm-radius-sm)', padding: '.8rem', textTransform: 'uppercase', letterSpacing: '.05em', fontSize: '.9rem' }}
        >
          {status === 'loading' ? 'Signing in…' : 'Sign In'}
        </button>

        <p style={{ fontSize: '.75rem', opacity: 0.55, margin: 0 }}>
          Demo accounts: admin@dronematrx.com &middot; operator@dronematrx.com &middot; observer@dronematrx.com &middot; analyst@dronematrx.com
          <br />
          Password: DroneMatrx@2026
        </p>
      </form>
    </div>
  )
}
