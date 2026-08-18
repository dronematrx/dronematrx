import { useEffect, useMemo, useState } from 'react'
import type { LatLngTuple } from 'leaflet'
import { MapContainer, Polygon, TileLayer, useMap } from 'react-leaflet'
import * as geofencesApi from '../api/geofences'
import * as missionsApi from '../api/missions'
import type { Drone, Geofence, Mission } from '../api/types'
import { AppShell } from '../components/layout/AppShell'
import { AoiDrawLayer } from '../components/map/AoiDrawLayer'
import { DroneMarker } from '../components/map/DroneMarker'
import { InstrumentRail } from '../components/map/InstrumentRail'
import { TelemetryPanel } from '../components/fleet/TelemetryPanel'
import { StatusPill } from '../components/ui/StatusPill'
import { useAuthStore } from '../store/useAuthStore'
import { useDroneStore } from '../store/useDroneStore'

const HQ_CENTER: LatLngTuple = [23.2156, 72.6369]

/** Leaflet snapshots container size at init and won't notice later layout
 * settling (fonts, flex resolution) unless nudged. */
function MapResizeHandler() {
  const map = useMap()
  useEffect(() => {
    const invalidate = () => map.invalidateSize()
    const timer = window.setTimeout(invalidate, 150)
    window.addEventListener('resize', invalidate)
    return () => {
      window.clearTimeout(timer)
      window.removeEventListener('resize', invalidate)
    }
  }, [map])
  return null
}

export function MapPage() {
  const drones = useDroneStore((s) => s.drones)
  const fetchDrones = useDroneStore((s) => s.fetchDrones)
  const connectTelemetry = useDroneStore((s) => s.connectTelemetry)
  const role = useAuthStore((s) => s.user?.role)
  const canWrite = role === 'ADMIN' || role === 'OPERATOR'

  const [geofences, setGeofences] = useState<Geofence[]>([])
  const [missions, setMissions] = useState<Mission[]>([])
  const [selectedMissionId, setSelectedMissionId] = useState<string>('')
  const [selectedDrone, setSelectedDrone] = useState<Drone | null>(null)

  const [showGeofences, setShowGeofences] = useState(true)
  const [showNfz, setShowNfz] = useState(true)
  const [showDrones, setShowDrones] = useState(true)

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
    const ring = [...draftPoints, draftPoints[0]].map(([lat, lng]) => [lng, lat])
    try {
      await missionsApi.setMissionAoi(selectedMissionId, { type: 'Polygon', coordinates: [ring] })
      setAoiStatus('AOI saved.')
      setDrawing(false)
      loadMissions()
    } catch (err) {
      setAoiStatus(err instanceof Error ? err.message : 'Failed to save AOI')
    }
  }

  return (
    <AppShell title="Flight Plan" fullBleed>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 320px) 1fr', height: '100%', minHeight: 0 }}>
        {/* LEFT CONTROL RAIL */}
        <aside style={{ background: 'var(--dm-navy-900)', padding: 'var(--dm-space-md)', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 'var(--dm-space-md)' }}>
          <div>
            <label style={{ fontSize: '.75rem', textTransform: 'uppercase', opacity: 0.7 }}>Mission</label>
            <select
              value={selectedMissionId}
              onChange={(e) => setSelectedMissionId(e.target.value)}
              style={{ width: '100%', marginTop: '.35rem', border: 0, borderRadius: 'var(--dm-radius-sm)', padding: '.55rem .7rem', background: 'var(--dm-surface-inset)', color: 'var(--dm-text-on-dark)' }}
            >
              <option value="">Select mission…</option>
              {missions.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
            {selectedMission && (
              <div style={{ marginTop: '.5rem' }}>
                <StatusPill label={selectedMission.status} />
              </div>
            )}
          </div>

          {canWrite && (
            <div style={{ background: 'var(--dm-slate-700)', borderRadius: 'var(--dm-radius-md)', padding: 'var(--dm-space-md)' }}>
              <div style={{ textTransform: 'uppercase', fontSize: '.85rem', marginBottom: '.5rem' }}>AOI Boundary&hellip;</div>
              <p style={{ fontSize: '.75rem', opacity: 0.75, margin: '0 0 .75rem' }}>
                Click the map to place vertices, then lock and save to the selected mission.
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '.4rem' }}>
                {!drawing ? (
                  <button type="button" onClick={startDrawing} style={buttonStyle}>
                    Draw Area (AOI)
                  </button>
                ) : (
                  <button type="button" onClick={() => setDrawing(false)} style={{ ...buttonStyle, background: 'var(--dm-status-pending)' }}>
                    Stop Drawing
                  </button>
                )}
                <div style={{ display: 'flex', gap: '.4rem' }}>
                  <button type="button" disabled={draftPoints.length < 3} onClick={saveAoi} style={{ ...buttonStyle, flex: 1, opacity: draftPoints.length < 3 ? 0.4 : 1 }}>
                    Save AOI
                  </button>
                  <button type="button" disabled={draftPoints.length === 0} onClick={clearDrawing} style={{ ...buttonStyle, flex: 1, background: 'var(--dm-status-alert)', opacity: draftPoints.length === 0 ? 0.4 : 1 }}>
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

          <div style={{ background: 'var(--dm-slate-700)', borderRadius: 'var(--dm-radius-md)', padding: 'var(--dm-space-md)' }}>
            <div style={{ textTransform: 'uppercase', fontSize: '.85rem', marginBottom: '.5rem' }}>Layers&hellip;</div>
            <LayerToggle label="Drones" checked={showDrones} onChange={setShowDrones} />
            <LayerToggle label="Geofences" checked={showGeofences} onChange={setShowGeofences} />
            <LayerToggle label="No-Fly Zones" checked={showNfz} onChange={setShowNfz} />
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
            Live Tiles<br />
            <span className="dm-mono">Operation: {selectedMission?.name ?? 'None Selected'}</span>
          </div>
        </aside>

        {/* CENTER MAP VIEWPORT */}
        <div style={{ position: 'relative' }}>
          <MapContainer center={HQ_CENTER} zoom={14} style={{ height: '100%', width: '100%' }} zoomControl={false}>
            <TileLayer
              attribution='&copy; OpenStreetMap contributors'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />

            {showGeofences &&
              geofences
                .filter((g) => g.type === 'geofence')
                .map((g) => (
                  <Polygon
                    key={g.id}
                    positions={g.geometry.coordinates[0].map(([lng, lat]) => [lat, lng] as LatLngTuple)}
                    pathOptions={{ color: '#4A5149', fillColor: '#4A5149', fillOpacity: 0.15, weight: 2 }}
                  />
                ))}

            {showNfz &&
              geofences
                .filter((g) => g.type === 'nfz')
                .map((g) => (
                  <Polygon
                    key={g.id}
                    positions={g.geometry.coordinates[0].map(([lng, lat]) => [lat, lng] as LatLngTuple)}
                    pathOptions={{ color: '#7A3B3B', fillColor: '#7A3B3B', fillOpacity: 0.2, weight: 2, dashArray: '6 4' }}
                  />
                ))}

            {selectedMission?.aoi_geometry && (
              <Polygon
                positions={selectedMission.aoi_geometry.coordinates[0].map(([lng, lat]) => [lat, lng] as LatLngTuple)}
                pathOptions={{ color: '#9C9FAB', fillColor: '#273046', fillOpacity: 0.25, weight: 2 }}
              />
            )}

            <AoiDrawLayer active={drawing} points={draftPoints} onAddPoint={(p) => setDraftPoints((prev) => [...prev, p])} />

            {showDrones && drones.map((d) => <DroneMarker key={d.id} drone={d} onSelect={setSelectedDrone} />)}

            <InstrumentRail />
            <MapResizeHandler />
          </MapContainer>

          {/* TELEMETRY FOOTER BAR - spec 6.7 */}
          <div
            style={{
              position: 'absolute',
              left: 0,
              right: 0,
              bottom: 0,
              zIndex: 20,
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

const buttonStyle: React.CSSProperties = {
  border: 0,
  borderRadius: 'var(--dm-radius-sm)',
  padding: '.55rem',
  background: 'var(--dm-status-active)',
  color: 'white',
  textTransform: 'uppercase',
  fontSize: '.75rem',
}
