import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Columns2, Link2, PlaneLanding, Home, Pause, Video, ScanSearch, Layers } from 'lucide-react'
import * as geofencesApi from '../api/geofences'
import * as mc from '../api/missionControl'
import type { FleetCommandName, HeatCell } from '../api/missionControl'
import type { Geofence } from '../api/types'
import { AppShell } from '../components/layout/AppShell'
import { GeofenceLayers } from '../components/map/GeofenceLayers'
import { MissionMap } from '../components/map/MissionMap'
import { BindingDialog } from '../components/missioncontrol/BindingDialog'
import { DetectionFeed } from '../components/missioncontrol/DetectionFeed'
import { DroneControl } from '../components/missioncontrol/DroneControl'
import { FleetCommandDialog } from '../components/missioncontrol/FleetCommandDialog'
import { FleetPanel } from '../components/missioncontrol/FleetPanel'
import { McMapLayers } from '../components/missioncontrol/McMapLayers'
import { liveFleet } from '../components/missioncontrol/liveFleet'
import { VideoWall } from '../components/missioncontrol/VideoWall'
import { isRealtimeConnected, realtimeEvents } from '../telemetry/realtimeEvents'
import { useAuthStore } from '../store/useAuthStore'
import { useDroneStore } from '../store/useDroneStore'
import { useMissionControlStore } from '../store/useMissionControlStore'

declare global {
  interface Window {
    __dmxDashboard?: { mountedAt: number; readyAt?: number; navToReadyMs?: number; mountToReadyMs?: number }
    __dmxTelemetryArrivals?: Record<string, number[]>
  }
}

/** Sprint 6: three-panel live mission control (fleet | map | video + detections). */
export function MissionControlPage() {
  const role = useAuthStore((s) => s.user?.role)
  const canWrite = role === 'ADMIN' || role === 'OPERATOR'
  const canReview = canWrite || role === 'ANALYST'

  const telemetry = useDroneStore((s) => s.drones)
  const fetchDrones = useDroneStore((s) => s.fetchDrones)
  const connectTelemetry = useDroneStore((s) => s.connectTelemetry)
  const store = useMissionControlStore()
  const { overview, overviewAt, selectedDroneId, select, layers, feeds, heatmapVersion } = store

  const [now, setNow] = useState(() => Date.now())
  const [rightTab, setRightTab] = useState<'video' | 'detections'>('video')
  const [focusVideo, setFocusVideo] = useState(false)
  const [bindOpen, setBindOpen] = useState(false)
  const [fleetCmd, setFleetCmd] = useState<FleetCommandName | null>(null)
  const [geofences, setGeofences] = useState<Geofence[]>([])
  const [gotoPicking, setGotoPicking] = useState(false)
  const [gotoTarget, setGotoTarget] = useState<[number, number] | null>(null)
  const [accepted, setAccepted] = useState<{ id: string; lat: number; lon: number; label: string; confidence: number }[]>([])
  const [heat, setHeat] = useState<{ cells: HeatCell[]; max: number } | null>(null)
  const [centreOn, setCentreOn] = useState<[number, number] | null>(null)
  const [wsUp, setWsUp] = useState(isRealtimeConnected())
  const [showLayers, setShowLayers] = useState(false)
  const mounted = useRef(0)

  // One shared WebSocket (the fleet store's provider) + mission-control events on it.
  useEffect(() => {
    mounted.current = performance.now()
    window.__dmxDashboard = { mountedAt: mounted.current }
    void fetchDrones()
    connectTelemetry()
    const unsub = store.subscribe()
    const onConn = (e: Event) => setWsUp((e as CustomEvent<boolean>).detail)
    realtimeEvents.addEventListener('connection', onConn)
    return () => {
      unsub()
      realtimeEvents.removeEventListener('connection', onConn)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once
  }, [])

  const { loadOverview, loadDetections, loadFeeds } = store
  useEffect(() => {
    void loadOverview()
    void loadDetections()
    void loadFeeds()
    geofencesApi.listGeofences().then(setGeofences).catch(() => undefined)
    const a = window.setInterval(() => void loadOverview(), 2000)
    const b = window.setInterval(() => void loadFeeds(), 6000)
    const c = window.setInterval(() => setNow(Date.now()), 1000)
    const g = window.setInterval(() => geofencesApi.listGeofences().then(setGeofences).catch(() => undefined), 15000)
    return () => [a, b, c, g].forEach((t) => window.clearInterval(t))
  }, [loadOverview, loadDetections, loadFeeds])

  // Accepted detections + heatmap: authoritative backend queries, refreshed on review.
  useEffect(() => {
    mc.acceptedGeojson().then((fc) => setAccepted(fc.features.map((f) => ({ id: f.properties.id, lat: f.geometry.coordinates[1], lon: f.geometry.coordinates[0], label: f.properties.label, confidence: f.properties.confidence })))).catch(() => undefined)
    if (layers.heatmap) mc.getHeatmap(20).then((h) => setHeat({ cells: h.cells, max: h.max })).catch(() => undefined)
  }, [heatmapVersion, layers.heatmap])

  const drones = useMemo(() => liveFleet(overview, overviewAt, telemetry, now), [overview, overviewAt, telemetry, now])
  const tracks = store.tracks
  const appendTracks = store.appendTracks
  useEffect(() => {
    appendTracks(drones.map((d) => ({ id: d.id, lat: d.latitude, lon: d.longitude, connected: d.connection_state === 'connected' })))
  }, [drones, appendTracks])
  const selected = drones.find((d) => d.id === selectedDroneId) ?? null

  // Telemetry arrival log (performance: update interval per drone).
  const lastSeen = useRef<Record<string, string>>({})
  useEffect(() => {
    const log = (window.__dmxTelemetryArrivals ??= {})
    for (const d of telemetry) {
      if (d.last_telemetry_at && lastSeen.current[d.id] !== d.last_telemetry_at) {
        lastSeen.current[d.id] = d.last_telemetry_at
        ;(log[d.id] ??= []).push(Date.now())
        if (log[d.id].length > 600) log[d.id].shift()
      }
    }
  }, [telemetry])

  // Dashboard ready: first overview with fleet rows rendered.
  useEffect(() => {
    if (overview && window.__dmxDashboard && window.__dmxDashboard.readyAt === undefined) {
      requestAnimationFrame(() => {
        const t = performance.now()
        window.__dmxDashboard = { ...window.__dmxDashboard!, readyAt: t, navToReadyMs: t, mountToReadyMs: t - mounted.current }
      })
    }
  }, [overview])

  const selectDrone = useCallback((id: string) => {
    select(id)
    const d = drones.find((x) => x.id === id)
    if (d?.latitude != null && d.longitude != null) setCentreOn([d.latitude, d.longitude])
  }, [select, drones])

  const benches = overview?.bench_sessions ?? {}
  const anyConnected = drones.some((d) => d.connection_state === 'connected')
  const staleCount = drones.filter((d) => d.telemetry_source !== 'none' && d.connection_state !== 'connected').length

  return (
    <AppShell
      title="Mission Control"
      fullBleed
      actions={
        <>
          <span className="dm-mono" style={{ fontSize: '.68rem', color: wsUp ? '#7BD88F' : '#FF8A80' }} data-testid="mc-ws-state">{wsUp ? 'LIVE LINK' : 'LINK DOWN'}</span>
          {staleCount > 0 && <span className="dm-mono" style={{ fontSize: '.68rem', color: '#FFD08A' }} data-testid="mc-stale-banner">{staleCount} STALE</span>}
          {canWrite && (
            <>
              <button type="button" className="dm-btn" onClick={() => setBindOpen(true)} data-testid="mc-open-binding"><Link2 size={13} /> Flight controllers</button>
              <button type="button" className="dm-btn" disabled={!anyConnected} onClick={() => setFleetCmd('HOLD_ALL')} data-testid="mc-hold-all"><Pause size={13} /> Hold all</button>
              <button type="button" className="dm-btn" disabled={!anyConnected} onClick={() => setFleetCmd('RTL_ALL')} data-testid="mc-rtl-all"><Home size={13} /> RTL all</button>
              <button type="button" className="dm-btn dm-btn--danger" disabled={!anyConnected} onClick={() => setFleetCmd('LAND_ALL')} data-testid="mc-land-all"><PlaneLanding size={13} /> Land all</button>
            </>
          )}
          {!canWrite && <span className="dm-mono" style={{ fontSize: '.68rem', opacity: 0.8 }} data-testid="mc-read-only">READ-ONLY ({role})</span>}
        </>
      }
    >
      <div className="dm-mc-grid" data-focus={focusVideo ? 'video' : 'map'} data-testid="mc-dashboard">
        <aside className="dm-mc-panel" aria-label="Fleet" data-testid="mc-left">
          {store.overviewError && <div className="dm-banner dm-banner--bad" style={{ fontSize: '.7rem' }} data-testid="mc-overview-error">{store.overviewError}</div>}
          <FleetPanel drones={drones} summary={overview?.summary ?? null} selectedId={selectedDroneId} onSelect={selectDrone} />
          {selected && (
            <DroneControl key={selected.id} drone={selected} canWrite={canWrite} gotoTarget={gotoTarget} gotoPicking={gotoPicking}
              setGotoPicking={setGotoPicking} clearGoto={() => { setGotoTarget(null); setGotoPicking(false) }} onBind={() => setBindOpen(true)} />
          )}
        </aside>

        <div className="dm-mc-map" data-testid="mc-map">
          <MissionMap drones={[]} center={[23.232, 72.643]} zoom={14}>
            {layers.geofences && <GeofenceLayers geofences={geofences} />}
            <McMapLayers drones={drones} selectedId={selectedDroneId} onSelect={selectDrone} centreOn={centreOn} showTracks={layers.tracks} tracks={tracks}
              accepted={accepted} showDetections={layers.detections} heat={layers.heatmap ? heat?.cells ?? [] : null} heatMax={heat?.max ?? 1}
              goto={{ active: gotoPicking, target: gotoTarget, onPick: (lat, lon) => { setGotoTarget([lat, lon]); setGotoPicking(false) } }} />
          </MissionMap>
          <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 1000 }}>
            <button type="button" className="dm-btn" onClick={() => setShowLayers(!showLayers)} aria-expanded={showLayers} data-testid="mc-layers-toggle"><Layers size={13} /> Layers</button>
            {showLayers && (
              <div className="dm-card" style={{ background: 'var(--dm-navy-900)', padding: '.4rem .6rem', marginTop: 4, fontSize: '.72rem', display: 'grid', gap: 3 }} data-testid="mc-layers">
                {(['tracks', 'geofences', 'detections', 'heatmap'] as const).map((k) => (
                  <label key={k} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input type="checkbox" checked={layers[k]} onChange={() => store.toggleLayer(k)} data-testid={`mc-layer-${k}`} />
                    {k === 'tracks' ? 'Actual tracks' : k === 'geofences' ? 'Geofences / NFZ' : k === 'detections' ? 'Accepted detections' : 'Detection heatmap'}
                  </label>
                ))}
              </div>
            )}
          </div>
          <div className="dm-mono" style={{ position: 'absolute', left: 0, right: 0, bottom: 0, zIndex: 1000, background: 'color-mix(in srgb, var(--dm-slate-500) 92%, transparent)', padding: '.4rem var(--dm-space-md)', display: 'flex', gap: 'var(--dm-space-md)', fontSize: '.68rem', flexWrap: 'wrap' }}>
            <span>DRONES {drones.filter((d) => d.latitude != null).length} ON MAP</span>
            <span>SITL {overview?.summary.by_source.sitl ?? 0} · DEMO {overview?.summary.by_source.kinematic_demo ?? 0} · HW {overview?.summary.by_source.hardware ?? 0}</span>
            <span>STALE AFTER {overview?.stale_after_s ?? '--'} s</span>
            {gotoPicking && <span style={{ color: '#FFD08A' }}>CLICK MAP: GO TO DESTINATION</span>}
            <span style={{ marginLeft: 'auto' }}>{selected ? `SELECTED ${selected.name}` : 'NO SELECTION'}</span>
          </div>
        </div>

        <aside className="dm-mc-panel" aria-label="Video and detections" data-testid="mc-right">
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <div className="dm-tabs" role="tablist" style={{ margin: 0 }}>
              <button type="button" role="tab" aria-selected={rightTab === 'video'} onClick={() => setRightTab('video')} data-testid="mc-tab-video"><Video size={12} /> Video wall</button>
              <button type="button" role="tab" aria-selected={rightTab === 'detections'} onClick={() => setRightTab('detections')} data-testid="mc-tab-detections">
                <ScanSearch size={12} /> Detections{store.detections.filter((d) => d.review_status === 'pending').length ? ` (${store.detections.filter((d) => d.review_status === 'pending').length})` : ''}
              </button>
            </div>
            <button type="button" className="dm-btn dm-btn--ghost" style={{ marginLeft: 'auto', padding: '.15rem .45rem' }} onClick={() => setFocusVideo(!focusVideo)} aria-pressed={focusVideo}
              title={focusVideo ? 'Compact panel' : 'Enlarge panel'} data-testid="mc-focus-video"><Columns2 size={12} /> {focusVideo ? 'Compact' : 'Expand'}</button>
          </div>
          {/* Both stay mounted so switching tabs never drops video sessions. */}
          <div style={{ display: rightTab === 'video' ? 'flex' : 'none', flexDirection: 'column', flex: 1, minHeight: 0 }}>
            {feeds ? (
              <VideoWall feeds={feeds.feeds} relay={feeds.relay} cameraConfigured={feeds.camera_configured} selectedDroneId={selectedDroneId} canWrite={canWrite}
                onSelectDrone={selectDrone} onRefresh={() => void loadFeeds()} />
            ) : <p style={{ fontSize: '.75rem' }}>Loading video sources…</p>}
          </div>
          <div style={{ display: rightTab === 'detections' ? 'flex' : 'none', flexDirection: 'column', flex: 1, minHeight: 0 }}>
            <DetectionFeed canReview={canReview} onLocate={(lat, lon) => setCentreOn([lat, lon])} />
          </div>
        </aside>
      </div>
      {bindOpen && (
        <BindingDialog drones={drones} initialDroneId={selected && (!selected.binding || selected.binding.state === 'connection_failed') ? selected.id : null}
          benches={benches} onClose={() => setBindOpen(false)} onBound={() => { void loadOverview(); void loadFeeds() }} />
      )}
      {fleetCmd && <FleetCommandDialog command={fleetCmd} onClose={() => { setFleetCmd(null); void loadOverview() }} />}
    </AppShell>
  )
}
