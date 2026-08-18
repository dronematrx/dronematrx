import { useEffect, useMemo, useState } from 'react'
import { AppShell } from '../components/layout/AppShell'
import { FilterBar, FilterField } from '../components/ui/FilterBar'
import { FleetDataTable } from '../components/fleet/FleetDataTable'
import { DroneDetailPanel } from '../components/fleet/DroneDetailPanel'
import { DroneFormModal } from '../components/fleet/DroneFormModal'
import { useAuthStore } from '../store/useAuthStore'
import { useDroneStore } from '../store/useDroneStore'

export function FleetPage() {
  const drones = useDroneStore((s) => s.drones)
  const loading = useDroneStore((s) => s.loading)
  const error = useDroneStore((s) => s.error)
  const fetchDrones = useDroneStore((s) => s.fetchDrones)
  const connectTelemetry = useDroneStore((s) => s.connectTelemetry)
  const role = useAuthStore((s) => s.user?.role)
  const canWrite = role === 'ADMIN' || role === 'OPERATOR'

  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)

  useEffect(() => {
    fetchDrones()
    connectTelemetry()
  }, [fetchDrones, connectTelemetry])

  const filtered = useMemo(() => {
    return drones.filter((d) => {
      if (statusFilter && d.status !== statusFilter) return false
      if (search && !`${d.name} ${d.model}`.toLowerCase().includes(search.toLowerCase())) return false
      return true
    })
  }, [drones, search, statusFilter])

  const statusOptions = useMemo(() => Array.from(new Set(drones.map((d) => d.status))), [drones])

  return (
    <AppShell
      title="Fleet"
      actions={
        canWrite ? (
          <button
            type="button"
            onClick={() => setShowCreate(true)}
            style={{ background: 'var(--dm-status-active)', color: 'white', border: 0, borderRadius: 'var(--dm-radius-sm)', padding: '.5rem 1rem', textTransform: 'uppercase', fontSize: '.8rem' }}
          >
            + New Drone
          </button>
        ) : undefined
      }
    >
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(280px, 340px) 1fr', gap: 'var(--dm-space-lg)', alignItems: 'start' }}>
        <div style={{ position: 'sticky', top: 'calc(5rem + var(--dm-space-lg))' }}>
          {selectedId ? (
            <DroneDetailPanel droneId={selectedId} onDeactivated={() => setSelectedId(null)} />
          ) : (
            <div style={{ background: 'var(--dm-navy-900)', borderRadius: 'var(--dm-radius-md)', padding: 'var(--dm-space-lg)', textAlign: 'center', opacity: 0.7 }}>
              Select a drone from the table to view telemetry, payloads, and maintenance history.
            </div>
          )}
        </div>

        <div>
          <FilterBar>
            <FilterField label="Search" value={search} onChange={setSearch} placeholder="Search name or model..." />
            <FilterField label="Status" value={statusFilter} onChange={setStatusFilter} options={statusOptions} />
          </FilterBar>

          {error && <p style={{ color: 'var(--dm-status-alert)' }}>{error}</p>}
          {loading && drones.length === 0 ? (
            <p style={{ opacity: 0.7 }}>Loading fleet…</p>
          ) : (
            <FleetDataTable drones={filtered} selectedId={selectedId} onSelect={(d) => setSelectedId(d.id)} />
          )}
        </div>
      </div>

      {showCreate && <DroneFormModal onClose={() => setShowCreate(false)} />}
    </AppShell>
  )
}
