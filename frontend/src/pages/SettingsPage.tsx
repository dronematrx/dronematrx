import { useEffect, useMemo, useState, type ChangeEvent } from 'react'
import type { LatLngTuple } from 'leaflet'
import { Download, Pencil, Trash2, Upload } from 'lucide-react'
import * as geofencesApi from '../api/geofences'
import type { Geofence, GeofenceType } from '../api/types'
import { AppShell } from '../components/layout/AppShell'
import { AoiDrawLayer, type DrawMode } from '../components/map/AoiDrawLayer'
import { GeofenceLayers } from '../components/map/GeofenceLayers'
import { MissionMap } from '../components/map/MissionMap'
import { GEOFENCE_LABEL, polygonBounds } from '../lib/mapGeometry'
import { StatusPill } from '../components/ui/StatusPill'
import { closedRing } from '../lib/format'
import { useAuthStore } from '../store/useAuthStore'

const TYPES: GeofenceType[] = ['inclusion', 'exclusion', 'nfz']

function download(filename: string, content: string, mime: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mime }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export function SettingsPage() {
  const role = useAuthStore((s) => s.user?.role)
  const canWrite = role === 'ADMIN' || role === 'OPERATOR'
  const [zones, setZones] = useState<Geofence[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [typeFilter, setTypeFilter] = useState<GeofenceType | ''>('')

  const [drawMode, setDrawMode] = useState<DrawMode>('polygon')
  const [drawing, setDrawing] = useState(false)
  const [points, setPoints] = useState<[number, number][]>([])
  const [form, setForm] = useState({ name: '', type: 'nfz' as GeofenceType, altitude_max: 120 })
  const [editing, setEditing] = useState<Geofence | null>(null)
  const [importType, setImportType] = useState<GeofenceType>('inclusion')

  const load = () =>
    geofencesApi
      .listGeofences()
      .then((z) => {
        setZones(z)
        setLoadError(null)
      })
      .catch((err) => setLoadError(err instanceof Error ? err.message : 'Failed to load geofences'))

  useEffect(() => {
    load()
  }, [])

  const shown = useMemo(() => zones.filter((z) => !typeFilter || z.type === typeFilter), [zones, typeFilter])
  const selected = zones.find((z) => z.id === selectedId) ?? null
  const fitTo = useMemo(() => (selected ? polygonBounds(selected.geometry.coordinates[0]) : null), [selected])

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setMessage(null)
    try {
      await fn()
      setMessage(ok)
      await load()
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Request failed')
    }
  }

  const create = () =>
    run(async () => {
      if (!form.name.trim()) throw new Error('Name is required')
      if (points.length < 3) throw new Error('Draw at least 3 vertices on the map')
      await geofencesApi.createGeofence({ name: form.name.trim(), type: form.type, altitude_max: form.altitude_max, geometry: { type: 'Polygon', coordinates: [closedRing(points)] } })
      setPoints([])
      setDrawing(false)
      setForm((f) => ({ ...f, name: '' }))
    }, 'Geofence created')

  const onImport = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    const text = await file.text()
    const format = file.name.toLowerCase().endsWith('.kml') ? 'kml' : 'geojson'
    await run(async () => {
      const result = await geofencesApi.importGeofences(format, text, importType)
      if (result.skipped.length) setMessage(`Imported ${result.created.length}; skipped ${result.skipped.length}: ${result.skipped.map((s) => s.reason).join('; ')}`)
    }, `Imported ${file.name}`)
  }

  const onExport = (format: 'geojson' | 'kml') =>
    run(async () => {
      const content = await geofencesApi.exportGeofences(format)
      download(`dronematrx-geofences.${format}`, content, format === 'kml' ? 'application/vnd.google-earth.kml+xml' : 'application/geo+json')
    }, `Exported ${format.toUpperCase()}`)

  return (
    <AppShell title="Settings" fullBleed>
      <div className="dm-two-col" style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 440px) 1fr', height: '100%', minHeight: 0 }}>
        <section className="dm-wizard-panel" aria-label="Geofence library">
          <h2 style={{ margin: 0, fontSize: '1.25rem', textTransform: 'uppercase', letterSpacing: '.06em' }}>Geofence Library&hellip;</h2>
          {loadError && <p className="dm-error">{loadError}</p>}
          {message && (
            <p style={{ fontSize: '.8rem', margin: 0 }} role="status" data-testid="geofence-message">
              {message}
            </p>
          )}

          <div style={{ display: 'flex', gap: 'var(--dm-space-xs)', flexWrap: 'wrap' }}>
            <select className="dm-input" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value as GeofenceType | '')} aria-label="Filter by type">
              <option value="">All types…</option>
              {TYPES.map((t) => (
                <option key={t} value={t}>
                  {GEOFENCE_LABEL[t]}
                </option>
              ))}
            </select>
            <button type="button" className="dm-btn" onClick={() => onExport('geojson')}>
              <Download size={14} /> GeoJSON
            </button>
            <button type="button" className="dm-btn" onClick={() => onExport('kml')}>
              <Download size={14} /> KML
            </button>
          </div>

          {canWrite && (
            <div className="dm-card" style={{ display: 'grid', gap: 'var(--dm-space-sm)' }}>
              <div className="dm-card-title">Import&hellip;</div>
              <div style={{ display: 'flex', gap: 'var(--dm-space-xs)' }}>
                <select className="dm-input" value={importType} onChange={(e) => setImportType(e.target.value as GeofenceType)} aria-label="Default type for untyped features">
                  {TYPES.map((t) => (
                    <option key={t} value={t}>
                      Default: {GEOFENCE_LABEL[t]}
                    </option>
                  ))}
                </select>
                <label className="dm-btn" style={{ cursor: 'pointer' }}>
                  <Upload size={14} /> GeoJSON / KML
                  <input type="file" accept=".geojson,.json,.kml" onChange={onImport} style={{ display: 'none' }} data-testid="geofence-import" />
                </label>
              </div>
            </div>
          )}

          {canWrite && (
            <div className="dm-card" style={{ display: 'grid', gap: 'var(--dm-space-sm)' }}>
              <div className="dm-card-title">New Zone&hellip;</div>
              <label className="dm-field">
                <span>Name</span>
                <input className="dm-input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} data-testid="geofence-name" />
              </label>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--dm-space-sm)' }}>
                <label className="dm-field">
                  <span>Type</span>
                  <select className="dm-input" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value as GeofenceType })} data-testid="geofence-type">
                    {TYPES.map((t) => (
                      <option key={t} value={t}>
                        {GEOFENCE_LABEL[t]}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="dm-field">
                  <span>Ceiling (m)</span>
                  <input className="dm-input dm-mono" type="number" value={form.altitude_max} onChange={(e) => setForm({ ...form, altitude_max: Number(e.target.value) })} />
                </label>
              </div>
              <div style={{ display: 'flex', gap: 'var(--dm-space-xs)' }}>
                <button type="button" className={`dm-btn ${drawing && drawMode === 'polygon' ? 'dm-btn--primary' : ''}`} onClick={() => { setDrawMode('polygon'); setPoints([]); setDrawing(true) }} data-testid="geofence-draw-polygon">
                  Polygon
                </button>
                <button type="button" className={`dm-btn ${drawing && drawMode === 'rectangle' ? 'dm-btn--primary' : ''}`} onClick={() => { setDrawMode('rectangle'); setPoints([]); setDrawing(true) }} data-testid="geofence-draw-rectangle">
                  Rectangle
                </button>
                <button type="button" className="dm-btn dm-btn--primary" style={{ marginLeft: 'auto' }} disabled={points.length < 3} onClick={create} data-testid="geofence-save">
                  Save Zone
                </button>
              </div>
              <span className="dm-mono" style={{ fontSize: '.7rem', opacity: 0.7 }}>
                {points.length} VERTICES
              </span>
            </div>
          )}

          <div style={{ display: 'grid', gap: '.35rem' }} data-testid="geofence-list">
            {shown.map((z) =>
              editing?.id === z.id ? (
                <div key={z.id} className="dm-card" style={{ display: 'grid', gap: '.4rem' }}>
                  <input className="dm-input" value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} aria-label="Zone name" />
                  <div style={{ display: 'flex', gap: '.4rem' }}>
                    <select className="dm-input" value={editing.type} onChange={(e) => setEditing({ ...editing, type: e.target.value as GeofenceType })} aria-label="Zone type">
                      {TYPES.map((t) => (
                        <option key={t} value={t}>
                          {GEOFENCE_LABEL[t]}
                        </option>
                      ))}
                    </select>
                    <input className="dm-input dm-mono" type="number" style={{ width: '6rem' }} value={editing.altitude_max ?? 120} onChange={(e) => setEditing({ ...editing, altitude_max: Number(e.target.value) })} aria-label="Ceiling" />
                    <label style={{ display: 'flex', gap: '.3rem', alignItems: 'center', fontSize: '.8rem' }}>
                      <input type="checkbox" checked={editing.active} onChange={(e) => setEditing({ ...editing, active: e.target.checked })} /> Active
                    </label>
                  </div>
                  <div style={{ display: 'flex', gap: '.4rem', justifyContent: 'flex-end' }}>
                    <button type="button" className="dm-btn dm-btn--ghost" onClick={() => setEditing(null)}>
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="dm-btn dm-btn--primary"
                      onClick={() =>
                        run(async () => {
                          await geofencesApi.updateGeofence(z.id, { name: editing.name, type: editing.type, altitude_max: editing.altitude_max, active: editing.active })
                          setEditing(null)
                        }, 'Geofence updated')
                      }
                    >
                      Save
                    </button>
                  </div>
                </div>
              ) : (
                <div
                  key={z.id}
                  className="dm-card"
                  onClick={() => setSelectedId(z.id)}
                  style={{ padding: '.5rem .75rem', display: 'flex', alignItems: 'center', gap: '.5rem', cursor: 'pointer', opacity: z.active ? 1 : 0.55, outline: z.id === selectedId ? '1px solid var(--dm-text-on-dark)' : undefined }}
                >
                  <StatusPill label={z.type} />
                  <span style={{ flex: 1, fontSize: '.85rem' }}>
                    {z.name}
                    {!z.active && ' (inactive)'}
                  </span>
                  <span className="dm-mono" style={{ fontSize: '.7rem', opacity: 0.75 }}>
                    ≤{z.altitude_max ?? '--'}M
                  </span>
                  {canWrite && (
                    <>
                      <button type="button" className="dm-btn dm-btn--ghost" style={{ padding: '.2rem .4rem' }} aria-label={`Edit ${z.name}`} onClick={(e) => { e.stopPropagation(); setEditing(z) }}>
                        <Pencil size={12} />
                      </button>
                      <button
                        type="button"
                        className="dm-btn dm-btn--ghost"
                        style={{ padding: '.2rem .4rem' }}
                        aria-label={`Delete ${z.name}`}
                        onClick={(e) => {
                          e.stopPropagation()
                          if (window.confirm(`Delete geofence "${z.name}"?`)) run(() => geofencesApi.deleteGeofence(z.id), 'Geofence deleted')
                        }}
                      >
                        <Trash2 size={12} />
                      </button>
                    </>
                  )}
                </div>
              ),
            )}
            {shown.length === 0 && <p style={{ fontSize: '.8rem', opacity: 0.7 }}>No geofences.</p>}
          </div>
        </section>
        <div style={{ position: 'relative', minHeight: '50vh' }}>
          <MissionMap showDrones={false} fitTo={fitTo} fitKey={selectedId}>
            <GeofenceLayers geofences={shown} highlightIds={selectedId ? [selectedId] : []} onSelect={(g) => setSelectedId(g.id)} />
            <AoiDrawLayer active={drawing} mode={drawMode} points={points as LatLngTuple[]} onAddPoint={(p) => setPoints((prev) => [...prev, p as [number, number]])} onReplacePoints={(pts) => setPoints(pts as [number, number][])} />
          </MissionMap>
        </div>
      </div>
    </AppShell>
  )
}
