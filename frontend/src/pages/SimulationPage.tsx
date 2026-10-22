import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import type { LatLngTuple } from 'leaflet'
import { CircleMarker, Polygon, Tooltip, useMapEvents } from 'react-leaflet'
import { X } from 'lucide-react'
import * as simApi from '../api/simulations'
import type { FailureType, SimCapacity, SimSpeed } from '../api/simulations'
import { TERMINAL_SIM_STATES } from '../api/simulations'
import { AppShell } from '../components/layout/AppShell'
import { AoiDrawLayer, type DrawMode } from '../components/map/AoiDrawLayer'
import { CbbaLanes } from '../components/map/CbbaLanes'
import { MissionMap } from '../components/map/MissionMap'
import { SimLegend, SimMapLayers } from '../components/simulation/SimMapLayers'
import { EventTimeline, FailurePanel, SimControls, SimDroneTable, SimulationBadge, SitlFleetTable } from '../components/simulation/SimRunPanels'
import { SimReportView } from '../components/simulation/SimReportView'
import { SimSetupPanel, type PickedPoint } from '../components/simulation/SimSetupPanel'
import { StatusPill } from '../components/ui/StatusPill'
import { polygonBounds, ringToLatLng } from '../lib/mapGeometry'
import { cssVar } from '../lib/tokens'
import { useAuthStore } from '../store/useAuthStore'
import { useDroneStore } from '../store/useDroneStore'
import { useMissionStore } from '../store/useMissionStore'
import { useSimulationStore } from '../store/useSimulationStore'

function ClickLayer({ active, onClick }: { active: boolean; onClick: (lat: number, lon: number) => void }) {
  useMapEvents({
    click(e) {
      if (active) onClick(e.latlng.lat, e.latlng.lng)
    },
  })
  return null
}

/** Sprint 5: AOI -> drones -> task -> CBBA -> Gazebo world -> PX4 SITL run ->
 * live map -> report, all inside the existing mission workflow. */
export function SimulationPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const role = useAuthStore((s) => s.user?.role)
  const canWrite = role === 'ADMIN' || role === 'OPERATOR'

  const drones = useDroneStore((s) => s.drones)
  const fetchDrones = useDroneStore((s) => s.fetchDrones)
  const connectTelemetry = useDroneStore((s) => s.connectTelemetry)

  const mission = useMissionStore()
  const { wizard, plan, missions } = mission
  const sim = useSimulationStore()
  const { session, live, tracks, events, busy, error } = sim

  const [capacity, setCapacity] = useState<SimCapacity | null>(null)
  const [fleetSize, setFleetSize] = useState(0)
  const [drawMode, setDrawMode] = useState<DrawMode>('polygon')
  const [drawing, setDrawing] = useState(false)
  const [draftPoints, setDraftPoints] = useState<[number, number][]>([])
  const [picking, setPicking] = useState(false)
  const [picked, setPicked] = useState<PickedPoint[]>([])
  const [obstaclePicking, setObstaclePicking] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    mission.openMission(id)
    mission.fetchMissions()
    fetchDrones()
    connectTelemetry()
    simApi.getSimFleet().then((f) => { setCapacity(f.capacity); setFleetSize(f.drones.length) }).catch(() => undefined)
    sim.reset()
    simApi.listSimSessions(id).then((list) => { if (list[0]) sim.load(list[0].id) }).catch(() => undefined)
    return () => mission.closeMission()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per mission
  }, [id])

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [])

  const state = live?.state && ['running', 'paused'].includes(live.state) ? live.state : session?.state
  // The simulation drives the mission's own state machine (ARMED -> ACTIVE ->
  // COMPLETED/ABORTED): refresh the mission header when the session state moves.
  const refreshMission = mission.refresh
  useEffect(() => {
    if (state) refreshMission()
  }, [state, refreshMission])
  const running = !!state && ['starting', 'running', 'paused'].includes(state)
  const finished = !!state && TERMINAL_SIM_STATES.includes(state as simApi.SimSessionState) && (session?.started_at || session?.has_report)
  const simDrones = useMemo(() => drones.filter((d) => d.simulated), [drones])

  const aoiRing = wizard?.aoi_geometry?.coordinates[0] ?? null
  const fitTo = useMemo(() => polygonBounds(aoiRing), [aoiRing])
  const fitKey = aoiRing ? `${id}:${aoiRing.length}:${aoiRing[0]?.join(',')}` : null

  const reloadSession = useCallback(async () => { if (sim.session) await sim.load(sim.session.id) }, [sim])

  const onStage = (count: number) =>
    sim.run('stage', async () => {
      await simApi.stageSimFleet(id, count)
      await Promise.all([mission.refresh(), fetchDrones()]) // refresh, not openMission: keeps the map mounted
      return true
    })

  const onGenerateWorld = ({ speed, dem }: { speed: SimSpeed; dem: 'terrarium' | 'flat' }) =>
    sim.run('world', async () => {
      let s = sim.session
      if (!s || ['failed', 'stopped', 'aborted', 'completed'].includes(s.state)) {
        s = await simApi.createSimSession({ mission_id: id, speed, dem_source: dem })
        sim.setSession(s)
      }
      await simApi.generateWorld(s.id)
      await sim.load(s.id)
      return true
    })

  const onPreflight = () => sim.run('preflight', async () => { await simApi.runPreflight(sim.session!.id); await reloadSession(); return true })
  const onStart = () => sim.run('start', async () => { await simApi.startSimulation(sim.session!.id); await reloadSession(); return true })

  const onInject = (type: FailureType, droneId: string | null, params: Record<string, unknown>) =>
    sim.run('failure', () => simApi.injectFailure(sim.session!.id, type, droneId, params))

  const onMapClick = (lat: number, lon: number) => {
    if (obstaclePicking) {
      setObstaclePicking(false)
      onInject('obstacle', null, { latitude: lat, longitude: lon })
    } else if (picking) {
      setPicked([...picked, { latitude: lat, longitude: lon }])
    }
  }

  if (!wizard) {
    return (
      <AppShell title="Simulation">
        <div className="dm-card" style={{ background: 'var(--dm-navy-900)' }}>{mission.error ? <p className="dm-error">{mission.error.message}</p> : 'Loading mission…'}</div>
      </AppShell>
    )
  }

  const err = error ?? mission.error
  return (
    <AppShell
      title={wizard.name}
      fullBleed
      actions={
        <>
          <SimulationBadge />
          <StatusPill label={wizard.status} />
          {session && <span className="dm-mono" style={{ fontSize: '.72rem' }} data-testid="sim-session-state">SESSION {String(state).toUpperCase()}</span>}
          <button type="button" className="dm-btn dm-btn--ghost" onClick={() => navigate('/missions')}><X size={14} /> Close</button>
        </>
      }
    >
      <div className="dm-wizard-grid" style={{ flex: 1, minHeight: 0 }}>
        <section className="dm-wizard-panel" aria-label="Simulation setup" style={{ overflow: 'auto' }}>
          {err && (
            <div className="dm-banner dm-banner--bad" role="alert" data-testid="sim-error" style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 400, fontSize: '.8rem' }}>
              <span style={{ flex: 1 }}>{err.code ? <strong className="dm-mono">{err.code}: </strong> : null}{err.message}</span>
              <button type="button" aria-label="Dismiss" onClick={() => { sim.clearError(); mission.clearError() }} style={{ background: 'transparent', border: 0, color: 'inherit' }}><X size={14} /></button>
            </div>
          )}
          {session?.state === 'failed' && session.error && (
            <div className="dm-banner dm-banner--bad" data-testid="sim-failed">Simulation failed ({session.error.stage}): {session.error.message}</div>
          )}
          {running && session ? (
            <>
              <SimControls live={live} state={state ?? 'starting'} busy={busy} canWrite={canWrite}
                onPause={() => sim.run('pause', () => simApi.pauseSimulation(session.id))}
                onResume={() => sim.run('resume', () => simApi.resumeSimulation(session.id))}
                onSpeed={(s) => sim.run('speed', () => simApi.setSimSpeed(session.id, s))}
                onStop={() => sim.run('stop', async () => { await simApi.stopSimulation(session.id); await reloadSession(); return true })}
                onAbort={() => sim.run('abort', async () => { await simApi.abortSimulation(session.id); await reloadSession(); return true })} />
              {state === 'starting' && <div className="dm-banner dm-banner--amber" data-testid="sim-starting">Starting Gazebo and {session.requested_drones} PX4 SITL instances…</div>}
              {canWrite && <FailurePanel instances={session.instances} busy={busy} canWrite={canWrite} obstaclePicking={obstaclePicking} setObstaclePicking={setObstaclePicking} onInject={onInject} />}
            </>
          ) : finished && session ? (
            <SimReportView sessionId={session.id} canWrite={canWrite}
              onRerun={() => sim.run('rerun', async () => { const r = await simApi.rerunSimulation(session.id); navigate(`/missions/${r.mission_id}/simulate`); return r })} />
          ) : (
            <SimSetupPanel
              wizard={wizard} plan={plan} missions={missions} capacity={capacity} fleetSize={fleetSize} session={session} busy={busy ?? (mission.busy ? String(mission.busy) : null)}
              readOnly={!canWrite} draw={{ mode: drawMode, setMode: setDrawMode, drawing, setDrawing, points: draftPoints, setPoints: setDraftPoints }}
              picking={picking} setPicking={setPicking} picked={picked} setPicked={setPicked}
              onSaveStep={(step, data) => mission.saveStep(step, data)} onStage={onStage} onAllocate={() => mission.runAllocation()}
              onGenerateWorld={onGenerateWorld} onPreflight={onPreflight} onStart={onStart}
            />
          )}
        </section>

        <div className="dm-wizard-map" style={{ position: 'relative', minHeight: 0 }} data-testid="sim-map">
          <MissionMap drones={[]} fitTo={fitTo} fitKey={fitKey}>
            {aoiRing && (
              <Polygon positions={ringToLatLng(aoiRing)} pathOptions={{ color: cssVar('--dm-aoi-stroke'), fillColor: cssVar('--dm-navy-900'), fillOpacity: 0.15, weight: 2 }} className="dm-sim-aoi" />
            )}
            {!running && !finished && plan && <CbbaLanes bundles={plan.bundles} tasks={plan.tasks} dimmed={plan.stale} />}
            {!running && !finished && simDrones.filter((d) => (wizard.config.sensors as { drone_ids?: string[] } | undefined)?.drone_ids?.includes(d.id)).map((d) =>
              d.latitude != null && d.longitude != null ? (
                <CircleMarker key={d.id} center={[d.latitude, d.longitude]} radius={5} pathOptions={{ color: '#FFFFFF', fillColor: '#8A6D3B', fillOpacity: 1, weight: 2 }}>
                  <Tooltip>{d.name} launch pad (SIMULATION)</Tooltip>
                </CircleMarker>
              ) : null,
            )}
            {(running || finished) && session && (
              <SimMapLayers instances={session.instances} live={live} tracks={tracks} tasks={plan?.tasks ?? []} drones={simDrones} now={now} />
            )}
            {picked.map((p, i) => (
              <CircleMarker key={i} center={[p.latitude, p.longitude] as LatLngTuple} radius={6} pathOptions={{ color: '#FFFFFF', fillColor: cssVar('--dm-status-armed'), fillOpacity: 1, weight: 2 }}>
                <Tooltip>{wizard.template === 'custom' ? 'W' : 'P'}{String(i + 1).padStart(3, '0')}</Tooltip>
              </CircleMarker>
            ))}
            <AoiDrawLayer active={drawing && canWrite} mode={drawMode} points={draftPoints as LatLngTuple[]}
              onAddPoint={(p) => setDraftPoints((prev) => [...prev, p as [number, number]])} onReplacePoints={(pts) => setDraftPoints(pts as [number, number][])} />
            <ClickLayer active={picking || obstaclePicking} onClick={onMapClick} />
          </MissionMap>
          <div className="dm-mono" style={{ position: 'absolute', left: 0, right: 0, bottom: 0, zIndex: 1000, background: 'color-mix(in srgb, var(--dm-slate-500) 92%, transparent)', padding: '.45rem var(--dm-space-md)', display: 'flex', gap: 'var(--dm-space-md)', fontSize: '.72rem', flexWrap: 'wrap', alignItems: 'center' }}>
            <span>SIMULATION</span>
            <span>DRONES {session?.spawned_drones || session?.requested_drones || 0}</span>
            <span>T {live ? `${live.sim_time_s.toFixed(0)} s` : '--'}</span>
            {(drawing || picking || obstaclePicking) && <span style={{ color: '#FFD08A' }}>{obstaclePicking ? 'CLICK MAP: OBSTACLE' : picking ? 'CLICK MAP: TARGET' : 'CLICK MAP: DRAW AOI'}</span>}
            {(running || finished) && <span style={{ marginLeft: 'auto' }}><SimLegend /></span>}
          </div>
        </div>

        <aside className="dm-wizard-panel" style={{ overflow: 'auto', display: 'grid', gap: 'var(--dm-space-sm)', alignContent: 'start' }} data-testid="sim-side">
          {session ? (
            <>
              <div className="dm-card-title" style={{ fontSize: '.8rem' }}>Fleet · live telemetry</div>
              <SimDroneTable instances={session.instances} live={running ? live : null} drones={simDrones} now={now} />
              {session.instances.some((i) => i.sitl_pid) && (
                <>
                  <div className="dm-card-title" style={{ fontSize: '.8rem' }}>PX4 SITL fleet · simhost process manager</div>
                  <SitlFleetTable instances={session.instances} />
                </>
              )}
              <div className="dm-card-title" style={{ fontSize: '.8rem' }}>Event timeline</div>
              <EventTimeline events={events} instances={session.instances} />
              {session.environment?.versions && (
                <div className="dm-mono" style={{ fontSize: '.68rem', opacity: 0.8 }} data-testid="sim-versions">
                  {session.environment.versions.gazebo} · PX4 {session.environment.versions.px4} · startup {session.environment.startup_s ?? '--'} s
                </div>
              )}
            </>
          ) : (
            <p style={{ fontSize: '.8rem', opacity: 0.8 }}>Configure the mission on the left. Drones, routes and telemetry of the running Gazebo simulation appear on this same map.</p>
          )}
        </aside>
      </div>
    </AppShell>
  )
}
