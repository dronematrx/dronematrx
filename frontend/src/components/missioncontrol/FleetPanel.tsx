import { useMemo, useState } from 'react'
import { Search } from 'lucide-react'
import type { FleetSummary } from '../../api/missionControl'
import { SOURCE_LABEL, ageLabel, liveSummary, type LiveDrone } from './liveFleet'

type Filter = 'all' | 'connected' | 'stale' | 'offline' | 'sitl' | 'kinematic_demo' | 'on_mission'
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' }, { id: 'connected', label: 'Connected' }, { id: 'stale', label: 'Stale' }, { id: 'offline', label: 'Offline' },
  { id: 'sitl', label: 'SITL' }, { id: 'kinematic_demo', label: 'Demo' }, { id: 'on_mission', label: 'On mission' },
]

interface Props {
  drones: LiveDrone[]
  summary: FleetSummary | null
  selectedId: string | null
  onSelect: (id: string) => void
}

export function FleetPanel({ drones, selectedId, onSelect }: Props) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const s = liveSummary(drones)
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return drones
      .filter((d) => !q || d.name.toLowerCase().includes(q) || (d.uin ?? '').toLowerCase().includes(q))
      .filter((d) => {
        switch (filter) {
          case 'all': return true
          case 'connected': return d.connection_state === 'connected'
          case 'stale': return d.connection_state === 'stale'
          case 'offline': return d.connection_state === 'offline' || d.connection_state === 'no_telemetry'
          case 'on_mission': return !!d.mission
          default: return d.telemetry_source === filter
        }
      })
      // Drones with a telemetry source first, then by name.
      .sort((a, b) => Number(a.telemetry_source === 'none') - Number(b.telemetry_source === 'none') || a.name.localeCompare(b.name))
  }, [drones, query, filter])

  return (
    <>
      <div className="dm-card-title" style={{ fontSize: '.8rem', margin: 0 }}>Fleet</div>
      <div className="dm-mc-summary" data-testid="mc-fleet-summary">
        <div><span>Registered</span><strong data-testid="mc-sum-total">{s.total}</strong></div>
        <div><span>Connected</span><strong data-testid="mc-sum-connected" style={{ color: '#7BD88F' }}>{s.connected}</strong></div>
        <div><span>Available</span><strong data-testid="mc-sum-available">{s.available}</strong></div>
        <div><span>On mission</span><strong data-testid="mc-sum-mission">{s.onMission}</strong></div>
        <div><span>Stale/offline</span><strong data-testid="mc-sum-offline" style={{ color: s.stale + s.offline ? '#FFD08A' : undefined }}>{s.stale + s.offline}</strong></div>
        <div><span>Failed</span><strong data-testid="mc-sum-failed" style={{ color: s.failed ? '#FF8A80' : undefined }}>{s.failed}</strong></div>
      </div>
      <label style={{ position: 'relative', display: 'block' }}>
        <Search size={13} style={{ position: 'absolute', left: 8, top: 9, opacity: 0.6 }} />
        <input className="dm-input" placeholder="Search drones" value={query} onChange={(e) => setQuery(e.target.value)} style={{ paddingLeft: 26, width: '100%' }} data-testid="mc-fleet-search" aria-label="Search drones" />
      </label>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }} role="tablist" aria-label="Fleet filter">
        {FILTERS.map((f) => (
          <button key={f.id} type="button" role="tab" aria-selected={filter === f.id} onClick={() => setFilter(f.id)} data-testid={`mc-filter-${f.id}`}
            className="dm-btn dm-btn--ghost" style={{ padding: '.15rem .45rem', fontSize: '.62rem', opacity: filter === f.id ? 1 : 0.6, outline: filter === f.id ? '1px solid #FFD08A' : undefined }}>
            {f.label}
          </button>
        ))}
      </div>
      <div style={{ display: 'grid', gap: 4 }} role="listbox" aria-label="Drones" data-testid="mc-fleet-list">
        {shown.map((d) => (
          <button key={d.id} type="button" role="option" aria-selected={d.id === selectedId} className="dm-mc-drone" data-conn={d.connection_state}
            onClick={() => onSelect(d.id)} data-testid={`mc-drone-${d.name}`} data-drone-id={d.id}>
            <span style={{ display: 'flex', gap: 5, alignItems: 'center', minWidth: 0 }}>
              <strong style={{ fontSize: '.78rem' }}>{d.name}</strong>
              <span className={`dm-src dm-src--${d.telemetry_source}`}>{SOURCE_LABEL[d.telemetry_source]}</span>
            </span>
            <span className={`dm-conn dm-conn--${d.connection_state}`} data-testid={`mc-conn-${d.name}`}>{d.connection_state.replace('_', ' ').toUpperCase()}</span>
            <span className="dm-mono" style={{ opacity: 0.85 }}>
              {d.telemetry_source === 'none' ? '—' : `${d.flight_mode}${d.armed ? ' · ARMED' : ''} · ${d.battery.toFixed(0)}%`}
              {d.binding?.state === 'connection_failed' && <span style={{ color: '#FF8A80' }}> · FC FAILED</span>}
            </span>
            <span className="dm-mono" style={{ opacity: 0.7, fontSize: '.62rem' }} data-testid={`mc-age-${d.name}`} title={d.last_telemetry_at ?? 'no telemetry'}>
              {ageLabel(d.ageMs)}
            </span>
            {d.mission && (
              <span style={{ gridColumn: '1 / -1', opacity: 0.8, fontSize: '.62rem' }}>
                {d.mission.mission_name} · task {d.mission.current_task ?? '—'} ({d.mission.tasks_done}/{d.mission.tasks_total})
              </span>
            )}
          </button>
        ))}
        {!shown.length && <p style={{ fontSize: '.72rem', opacity: 0.7 }}>No drones match.</p>}
      </div>
    </>
  )
}
