import { useEffect, useMemo, useState } from 'react'
import type { LatLngTuple } from 'leaflet'
import { Polygon } from 'react-leaflet'
import * as geofencesApi from '../api/geofences'
import * as missionsApi from '../api/missions'
import type { Drone, Geofence, GeofenceType, Mission, MissionPlan } from '../api/types'
import { AppShell } from '../components/layout/AppShell'
import { AoiDrawLayer } from '../components/map/AoiDrawLayer'
import { CbbaLanes } from '../components/map/CbbaLanes'
import { GeofenceLayers } from '../components/map/GeofenceLayers'
import { ringToLatLng } from '../lib/mapGeometry'
import { MissionMap } from '../components/map/MissionMap'
import { OrcaLayer } from '../components/map/OrcaLayer'
import { DemoControls, OrcaStatsCard, OrcaTimeline, PredictionBanner, RthQueueCard } from '../components/ops/OrcaPanels'
import { useOpsStore } from '../store/useOpsStore'
import { TelemetryPanel } from '../components/fleet/TelemetryPanel'
import { StatusPill } from '../components/ui/StatusPill'
import { closedRing } from '../lib/format'
import { cssVar } from '../lib/tokens'
import { useAuthStore } from '../store/useAuthStore'
import { useDroneStore } from '../store/useDroneStore'

export function MapPage() {
  const drones = useDroneStore((s) => s.drones)
  const fetchDrones = useDroneStore((s) => s.fetchDrones)
  const connectTelemetry = useDroneStore((s) => s.connectTelemetry)
  const role = useAuthStore((s) => s.user?.role)
  const canWrite = role === 'ADMIN' || role === 'OPERATOR'

  const [geofences, setGeofences] = useState<Geofence[]>([])
  const [missions, setMissions] = useState<Mission[]>([])
  const [selectedMissionId, setSelectedMissionId] = useState<string>('')
  const [plan, setPlan] = useState<MissionPlan | null>(null)
  const [selectedDrone, setSelectedDrone] = useState<Drone | null>(null)

  const orca = useOpsStore((s) => s.orca)
  const timeline = useOpsStore((s) => s.timeline)
  const opsBusy = useOpsStore((s) => s.busy)
  const opsError = useOpsStore((s) => s.error)
  const refreshOps = useOpsStore((s) => s.refresh)
  const startDemo = useOpsStore((s) => s.startDemo)
  const stopDemo = useOpsStore((s) => s.stopDemo)
  const [fitDemo, setFitDemo] = useState<string | null>(null)
  const [fitArea, setFitArea] = useState<'conflict' | 'all'>('conflict')
  const frame = (area: 'conflict' | 'all') => {
    setFitArea(area)
    setFitDemo(`${area}-${Date.now()}`)
  }

  const [layers, setLayers] = useState<Record<GeofenceType | 'drones' | 'lanes' | 'orca', boolean>>({
    orca: true,
    drones: true,
    inclusion: true,
    exclusion: true,
    nfz: true,
    lanes: true,
  })

  const [drawing, setDrawing] = useState(false)
  const [draftPoints, setDraftPoints] = useState<LatLngTuple[]>([])
  const [aoiStatus, setAoiStatus] = useState<string | null>(null)

  const loadGeofences = () => geofencesApi.listGeofences().then(setGeofences)
  const loadMissions = () => missionsApi.listMissions().then(setMissions)

  useEffect(() => {
    fetchDrones()
    connectTelemetry()
    loadGeofences()
    loadMissions()
    refreshOps()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const selectMission = (id: string) => {
    setSelectedMissionId(id)
    setPlan(null)
    if (id) missionsApi.getPlan(id).then(setPlan).catch(() => setPlan(null))
  }

  const selectedMission = useMemo(() => missions.find((m) => m.id === selectedMissionId) ?? null, [missions, selectedMissionId])

  const startDrawing = () => {
    setDrawing(true)
    setDraftPoints([])
    setAoiStatus(null)
  }

  const clearDrawing = () => {
    setDrawing(false)
    setDraftPoints([])
    setAoiStatus(null)
  }

  const saveAoi = async () => {
    if (!selectedMissionId) {
      setAoiStatus('Select a mission first.')
      return
    }
    if (draftPoints.length < 3) {
      setAoiStatus('Need at least 3 points.')
      return
    }
    try {
      await missionsApi.setMissionAoi(selectedMissionId, { type: 'Polygon', coordinates: [closedRing(draftPoints as [number, number][])] })
      setAoiStatus('AOI saved.')
      setDrawing(false)
      loadMissions()
    } catch (err) {
      setAoiStatus(err instanceof Error ? err.message : 'Failed to save AOI')
    }
  }

  const toggle = (key: keyof typeof layers) => (v: boolean) => setLayers((prev) => ({ ...prev, [key]: v }))

  return (
    <AppShell title="Flight Plan" fullBleed>
      <div className="dm-two-col" style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 320px) 1fr', height: '100%', minHeight: 0 }}>
        {/* LEFT CONTROL RAIL */}
        <aside style={{ background: 'var(--dm-navy-900)', padding: 'var(--dm-space-md)', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 'var(--dm-space-md)' }}>
          <div>
            <label style={{ fontSize: '.75rem', textTransform: 'uppercase', opacity: 0.7 }}>Mission</label>
            <select value={selectedMissionId} onChange={(e) => selectMission(e.target.value)} className="dm-input" style={{ width: '100%', marginTop: '.35rem' }}>
              <option value="">Select mission…</option>
              {missions.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
            {selectedMission && (
              <div style={{ marginTop: '.5rem', display: 'flex', gap: '.5rem', alignItems: 'center' }}>
                <StatusPill label={selectedMission.status} />
                {plan && (
                  <span className="dm-mono" style={{ fontSize: '.75rem', opacity: 0.8 }}>
                    {plan.bundles.length} lanes · {plan.coverage_pct.toFixed(0)}% cov
                  </span>
                )}
              </div>
            )}
          </div>

          {canWrite && (
            <div className="dm-card">
              <div className="dm-card-title">AOI Boundary&hellip;</div>
              <p style={{ fontSize: '.75rem', opacity: 0.75, margin: '0 0 .75rem' }}>
                Click the map to place vertices, then lock and save to the selected mission.
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '.4rem' }}>
                {!drawing ? (
                  <button type="button" onClick={startDrawing} className="dm-btn dm-btn--primary">
                    Draw Area (AOI)
                  </button>
                ) : (
                  <button type="button" onClick={() => setDrawing(false)} className="dm-btn">
                    Stop Drawing
                  </button>
                )}
                <div style={{ display: 'flex', gap: '.4rem' }}>
                  <button type="button" disabled={draftPoints.length < 3} onClick={saveAoi} className="dm-btn dm-btn--primary" style={{ flex: 1 }}>
                    Save AOI
                  </button>
                  <button type="button" disabled={draftPoints.length === 0} onClick={clearDrawing} className="dm-btn dm-btn--danger" style={{ flex: 1 }}>
                    Clear
                  </button>
                </div>
              </div>
              {draftPoints.length > 0 && (
                <p className="dm-mono" style={{ fontSize: '.7rem', opacity: 0.6, marginTop: '.5rem' }}>
                  VERTICES: {draftPoints.length} {draftPoints.length >= 3 ? '(valid polygon)' : '(need 3+)'}
                </p>
              )}
              {aoiStatus && <p style={{ fontSize: '.75rem', marginTop: '.5rem' }}>{aoiStatus}</p>}
            </div>
          )}

          {layers.orca && orca && <PredictionBanner view={orca} />}
          <DemoControls
            view={orca}
            canWrite={canWrite}
            busy={opsBusy}
            onStart={() => {
              startDemo()
              frame('conflict')
            }}
            onStop={stopDemo}
          />
          <div style={{ display: 'flex', gap: '.4rem' }}>
            <button type="button" className="dm-btn" style={{ flex: 1 }} onClick={() => frame('conflict')} data-testid="fit-conflict">
              Conflict zone
            </button>
            <button type="button" className="dm-btn" style={{ flex: 1 }} onClick={() => frame('all')} data-testid="fit-all">
              All drones
            </button>
          </div>
          {opsError && <p className="dm-error">{opsError}</p>}
          {orca && <OrcaStatsCard view={orca} />}
          {orca && <RthQueueCard view={orca} />}
          <OrcaTimeline events={timeline} view={orca} />

          <div className="dm-card">
            <div className="dm-card-title">Layers&hellip;</div>
            <LayerToggle label="ORCA Deconfliction" checked={layers.orca} onChange={toggle('orca')} />
            <LayerToggle label="Drones" checked={layers.drones} onChange={toggle('drones')} />
            <LayerToggle label="Inclusion Geofences" checked={layers.inclusion} onChange={toggle('inclusion')} />
            <LayerToggle label="Exclusion Zones" checked={layers.exclusion} onChange={toggle('exclusion')} />
            <LayerToggle label="No-Fly Zones" checked={layers.nfz} onChange={toggle('nfz')} />
            <LayerToggle label="CBBA Lanes" checked={layers.lanes} onChange={toggle('lanes')} />
          </div>

          {selectedDrone && (
            <TelemetryPanel
              cards={[
                {
                  title: selectedDrone.name,
                  rows: [
                    { label: 'Status', value: selectedDrone.status },
                    { label: 'Battery', value: `${selectedDrone.battery.toFixed(0)}%` },
                    { label: 'Altitude', value: `${selectedDrone.altitude.toFixed(0)} m` },
                    { label: 'Mode', value: selectedDrone.flight_mode },
                  ],
                },
              ]}
            />
          )}

          <div style={{ marginTop: 'auto', fontSize: '.7rem', opacity: 0.5, textTransform: 'uppercase' }}>
            Live Tiles
            <br />
            <span className="dm-mono">Operation: {selectedMission?.name ?? 'None Selected'}</span>
          </div>
        </aside>

        {/* CENTER MAP VIEWPORT */}
        <div style={{ position: 'relative', minHeight: '60vh' }}>
          <MissionMap
            drones={drones}
            showDrones={layers.drones && !(layers.orca && orca)}
            onDroneSelect={setSelectedDrone}
            fitTo={
              !fitDemo
                ? null
                : fitArea === 'conflict'
                  ? [[23.2156 - 0.0003, 72.6369 - 0.0013], [23.2156 + 0.0011, 72.6369 + 0.0017]]
                  : [[23.2156 - 0.0017, 72.6369 - 0.0018], [23.2156 + 0.0024, 72.6369 + 0.0019]]
            }
            fitKey={fitDemo}
            fitMaxZoom={18}
          >
            {layers.orca && orca && <OrcaLayer view={orca} timeline={timeline} />}
            <GeofenceLayers geofences={geofences} visible={{ inclusion: layers.inclusion, exclusion: layers.exclusion, nfz: layers.nfz }} />
            {selectedMission?.aoi_geometry && (
              <Polygon
                positions={ringToLatLng(selectedMission.aoi_geometry.coordinates[0])}
                pathOptions={{ color: cssVar('--dm-aoi-stroke'), fillColor: cssVar('--dm-navy-900'), fillOpacity: 0.25, weight: 2 }}
              />
            )}
            {layers.lanes && plan && <CbbaLanes bundles={plan.bundles} tasks={plan.tasks} />}
            <AoiDrawLayer active={drawing} points={draftPoints} onAddPoint={(p) => setDraftPoints((prev) => [...prev, p])} />
          </MissionMap>

          {/* TELEMETRY FOOTER BAR - spec 6.7 */}
          <div
            style={{
              position: 'absolute',
              left: 0,
              right: 0,
              bottom: 0,
              zIndex: 1000,
              background: 'color-mix(in srgb, var(--dm-slate-500) 92%, transparent)',
              color: 'var(--dm-text-on-dark)',
              padding: '.5rem var(--dm-space-md)',
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: '.75rem',
            }}
          >
            <span>
              Fleet Active: <strong className="dm-mono">{drones.filter((d) => d.status !== 'offline').length}</strong> / {drones.length}
            </span>
            <span className="dm-mono">WGS 84 &middot; HAE {selectedDrone?.altitude.toFixed(0) ?? '--'}M</span>
          </div>
        </div>
      </div>
    </AppShell>
  )
}

function LayerToggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: '.5rem', fontSize: '.85rem', padding: '.25rem 0' }}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  )
}
