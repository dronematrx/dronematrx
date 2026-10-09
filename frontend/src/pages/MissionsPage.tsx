import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Mission } from '../api/types'
import { AppShell } from '../components/layout/AppShell'
import { FilterBar, FilterField } from '../components/ui/FilterBar'
import { StatusPill } from '../components/ui/StatusPill'
import { formatArea } from '../lib/format'
import { useAuthStore } from '../store/useAuthStore'
import { useMissionStore } from '../store/useMissionStore'

const STATUSES = ['draft', 'planning', 'validating', 'armed', 'active', 'completed', 'aborted']

/** Tiny inline AOI preview: the table's required visual reference column (spec 5.5). */
export function AoiThumbnail({ mission, size = 44 }: { mission: Mission; size?: number }) {
  const ring = mission.aoi_geometry?.coordinates[0]
  if (!ring || ring.length < 3) {
    return (
      <div aria-label="No AOI" style={{ width: size, height: size, borderRadius: 'var(--dm-radius-sm)', background: 'var(--dm-surface-inset)', display: 'grid', placeItems: 'center', fontSize: '.6rem', opacity: 0.6 }}>
        NO AOI
      </div>
    )
  }
  const xs = ring.map((c) => c[0])
  const ys = ring.map((c) => c[1])
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
  const span = Math.max(maxX - minX, maxY - minY) || 1
  const pts = ring.map(([x, y]) => `${(((x - minX) / span) * 0.8 + 0.1) * size},${(1 - (((y - minY) / span) * 0.8 + 0.1)) * size}`).join(' ')
  return (
    <svg width={size} height={size} role="img" aria-label="AOI preview" style={{ background: 'var(--dm-surface-inset)', borderRadius: 'var(--dm-radius-sm)' }}>
      <polygon points={pts} fill="var(--dm-slate-500)" stroke="var(--dm-aoi-stroke)" strokeWidth="1.5" />
    </svg>
  )
}

/** Spec 6.1 Mission Card: thumbnail, status label, timing/action row. */
function MissionCard({ mission, onOpen, canWrite }: { mission: Mission; onOpen: () => void; canWrite: boolean }) {
  const resumable = ['draft', 'planning', 'validating'].includes(mission.status)
  return (
    <div className="dm-card" style={{ background: 'var(--dm-navy-900)', display: 'grid', gap: 'var(--dm-space-sm)' }}>
      <AoiThumbnail mission={mission} size={96} />
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '.5rem' }}>
        <strong style={{ textTransform: 'uppercase', letterSpacing: '.05em' }}>{mission.name}</strong>
        <StatusPill label={mission.status} />
      </div>
      <div className="dm-mono" style={{ fontSize: '.75rem', opacity: 0.75 }}>
        STEP {Math.min(mission.wizard_step, 6)}/6 · {formatArea(mission.aoi_area_sq_meters)}
      </div>
      <button type="button" className="dm-btn dm-btn--primary" onClick={onOpen} data-testid="open-mission">
        {resumable && canWrite ? 'Resume Planning' : 'Open Mission'}
      </button>
    </div>
  )
}

export function MissionsPage() {
  const navigate = useNavigate()
  const role = useAuthStore((s) => s.user?.role)
  const canWrite = role === 'ADMIN' || role === 'OPERATOR'
  const { missions, missionsLoading, missionsError, fetchMissions, createMission } = useMissionStore()

  const [filter, setFilter] = useState('')
  const [status, setStatus] = useState('')
  const [owner, setOwner] = useState('')
  const [date, setDate] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [newName, setNewName] = useState('')
  const [createError, setCreateError] = useState<string | null>(null)

  useEffect(() => {
    fetchMissions()
  }, [fetchMissions])

  const owners = useMemo(() => Array.from(new Set(missions.map((m) => (m.created_by ?? 'system').slice(0, 8)))), [missions])
  const filtered = useMemo(
    () =>
      missions.filter((m) => {
        if (filter && !`${m.name} ${m.mission_type} ${m.template ?? ''}`.toLowerCase().includes(filter.toLowerCase())) return false
        if (status && m.status !== status) return false
        if (owner && (m.created_by ?? 'system').slice(0, 8) !== owner) return false
        if (date && !m.created_at.startsWith(date)) return false
        return true
      }),
    [missions, filter, status, owner, date],
  )
  const selected = missions.find((m) => m.id === selectedId) ?? filtered[0] ?? null

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!newName.trim()) return
    setCreateError(null)
    try {
      const mission = await createMission(newName.trim())
      navigate(`/missions/${mission.id}/plan`)
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create mission')
    }
  }

  return (
    <AppShell title="Missions">
      <div className="dm-two-col" style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 320px) 1fr', gap: 'var(--dm-space-lg)', alignItems: 'start' }}>
        <div style={{ display: 'grid', gap: 'var(--dm-space-md)' }}>
          {canWrite && (
            <form onSubmit={submit} className="dm-card" style={{ background: 'var(--dm-navy-900)', display: 'grid', gap: 'var(--dm-space-sm)' }}>
              <div className="dm-card-title">New Mission&hellip;</div>
              <label className="dm-field">
                <span>Mission name</span>
                <input className="dm-input" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Operation codename" data-testid="new-mission-name" />
              </label>
              <button type="submit" className="dm-btn dm-btn--primary" disabled={!newName.trim()} data-testid="create-mission">
                Start Planning
              </button>
              {createError && <p className="dm-error">{createError}</p>}
            </form>
          )}
          {selected && <MissionCard mission={selected} canWrite={canWrite} onOpen={() => navigate(`/missions/${selected.id}/plan`)} />}
        </div>

        <div>
          <FilterBar>
            <FilterField label="Filter" value={filter} onChange={setFilter} placeholder="Filter..." />
            <FilterField label="Status" value={status} onChange={setStatus} options={STATUSES} />
            <FilterField label="Owner" value={owner} onChange={setOwner} options={owners} />
            <FilterField label="Date" value={date} onChange={setDate} placeholder="Date (YYYY-MM-DD)..." />
          </FilterBar>
          {missionsError && <p className="dm-error">{missionsError}</p>}
          {missionsLoading && missions.length === 0 ? (
            <p style={{ opacity: 0.7 }}>Loading missions…</p>
          ) : (
            <div style={{ overflowX: 'auto', borderRadius: 'var(--dm-radius-md)' }}>
              <table className="dm-mission-table">
                <thead>
                  <tr>
                    <th scope="col" aria-label="AOI preview" />
                    <th scope="col">Mission</th>
                    <th scope="col">Template</th>
                    <th scope="col">Status</th>
                    <th scope="col">Planning</th>
                    <th scope="col">Date / Time</th>
                    <th scope="col">Owner</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((m) => (
                    <tr
                      key={m.id}
                      onClick={() => setSelectedId(m.id)}
                      onDoubleClick={() => navigate(`/missions/${m.id}/plan`)}
                      style={{ cursor: 'pointer', outline: m.id === selected?.id ? '2px solid var(--dm-slate-400)' : undefined }}
                      data-testid="mission-row"
                    >
                      <td>
                        <AoiThumbnail mission={m} />
                      </td>
                      <td style={{ fontWeight: 600 }}>{m.name}</td>
                      <td style={{ textTransform: 'uppercase' }}>{m.template ?? m.mission_type}</td>
                      <td>
                        <StatusPill label={m.status} />
                      </td>
                      <td className="dm-mono">
                        {m.completed_steps.length}/5 steps
                        {m.validation_passed === false && ' · ⚠'}
                      </td>
                      <td className="dm-mono">{new Date(m.created_at).toLocaleString()}</td>
                      <td className="dm-mono">{(m.created_by ?? 'system').slice(0, 8)}</td>
                    </tr>
                  ))}
                  {filtered.length === 0 && (
                    <tr>
                      <td colSpan={7} style={{ padding: 'var(--dm-space-lg)', textAlign: 'center', opacity: 0.6 }}>
                        No missions match the current filters.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </AppShell>
  )
}
