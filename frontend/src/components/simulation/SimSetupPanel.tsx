import { useEffect, useState } from 'react'
import { CheckCircle2, Circle, Cpu, Globe2, Play, Plane, XCircle } from 'lucide-react'
import type { FlightParams, Mission, MissionPlan, MissionTemplate, WizardState } from '../../api/types'
import type { PreflightCheck, SimCapacity, SimSession, SimSpeed } from '../../api/simulations'
import type { DrawMode } from '../map/AoiDrawLayer'
import { closedRing, formatArea, formatDistance, formatDuration, formatPct } from '../../lib/format'
import { polygonAreaM2 } from '../../lib/geoArea'

const SIM_TASK_TYPES: { key: MissionTemplate; label: string; hint: string }[] = [
  { key: 'survey', label: 'Area survey', hint: 'Lawnmower coverage lanes inside the AOI' },
  { key: 'search', label: 'Search & rescue', hint: 'AOI split into search cells, each swept with a creeping line' },
  { key: 'agriculture', label: 'Agriculture survey', hint: 'Parallel crop rows at a fixed lane spacing' },
  { key: 'inspection', label: 'Infrastructure inspection', hint: 'Route through inspection targets picked on the map' },
  { key: 'custom', label: 'Custom waypoint mission', hint: 'Ordered waypoints picked on the map' },
]
const DRONE_COUNTS = [1, 2, 3, 4, 8, 16]
const SPEEDS: SimSpeed[] = [1, 2, 5, 10]

export interface DrawControls {
  mode: DrawMode
  setMode: (m: DrawMode) => void
  drawing: boolean
  setDrawing: (v: boolean) => void
  points: [number, number][]
  setPoints: (p: [number, number][]) => void
}

export interface PickedPoint {
  latitude: number
  longitude: number
}

interface Props {
  wizard: WizardState
  plan: MissionPlan | null
  missions: Mission[]
  capacity: SimCapacity | null
  fleetSize: number
  session: SimSession | null
  busy: string | null
  readOnly: boolean
  draw: DrawControls
  picking: boolean
  setPicking: (v: boolean) => void
  picked: PickedPoint[]
  setPicked: (p: PickedPoint[]) => void
  onSaveStep: (step: number, data: unknown) => Promise<unknown>
  onStage: (count: number) => Promise<unknown>
  onAllocate: () => Promise<unknown>
  onGenerateWorld: (opts: { speed: SimSpeed; dem: 'terrarium' | 'flat' }) => Promise<unknown>
  onPreflight: () => Promise<unknown>
  onStart: () => Promise<unknown>
}

function Section({ n, title, done, children, testId }: { n: number; title: string; done: boolean; children: React.ReactNode; testId: string }) {
  return (
    <div className="dm-card" style={{ background: 'var(--dm-navy-900)', padding: 'var(--dm-space-sm) var(--dm-space-md)', display: 'grid', gap: '.5rem' }} data-testid={testId}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', fontSize: '.8rem', textTransform: 'uppercase', letterSpacing: '.05em' }}>
        {done ? <CheckCircle2 size={14} color="var(--dm-lane-2)" /> : <Circle size={14} opacity={0.6} />}
        <span className="dm-mono">{n}</span> {title}
      </div>
      {children}
    </div>
  )
}

export function SimSetupPanel(props: Props) {
  const { wizard, plan, missions, capacity, fleetSize, session, busy, readOnly, draw, picking, setPicking, picked, setPicked } = props
  const cfg = wizard.config
  const template = (wizard.template ?? 'survey') as MissionTemplate
  const flight = (cfg.flight ?? {}) as Partial<FlightParams>
  const constraints = (cfg.constraints ?? {}) as Record<string, unknown>
  const selectedDrones = ((cfg.sensors as { drone_ids?: string[] } | undefined)?.drone_ids ?? []).length
  const maxDrones = Math.min(fleetSize || 16, capacity?.max_instances ?? 16)

  const [count, setCount] = useState(selectedDrones || 3)
  const [form, setForm] = useState({
    altitude_m: flight.altitude_m ?? 40,
    speed_mps: flight.speed_mps ?? 8,
    overlap_pct: flight.overlap_pct ?? 60,
    lane_spacing_m: flight.lane_spacing_m ?? 15,
    sar_cell_size_m: flight.sar_cell_size_m ?? 120,
    turn_behaviour: flight.turn_behaviour ?? 'coordinated_turn',
    rth_battery_pct: flight.rth_battery_pct ?? 25,
    rth_on_complete: flight.rth_on_complete ?? true,
    max_flight_time_min: (constraints.max_flight_time_min as number | null) ?? 20,
    link_loss_policy: (constraints.link_loss_policy as string) ?? 'RTH',
    min_battery_pct: (constraints.min_battery_pct as number) ?? 40,
  })
  const [speed, setSpeed] = useState<SimSpeed>(((session?.config?.speed as number) ?? 1) as SimSpeed)
  const [dem, setDem] = useState<'terrarium' | 'flat'>(((session?.config?.dem_source as string) ?? 'terrarium') as 'terrarium' | 'flat')
  const [showGeo, setShowGeo] = useState(false)

  useEffect(() => {
    // Restore picked targets/waypoints when the mission is reopened.
    const saved = template === 'custom' ? (constraints.custom_waypoints as PickedPoint[] | undefined) : (constraints.inspection_points as PickedPoint[] | undefined)
    if (saved?.length && picked.length === 0) setPicked(saved.map((p) => ({ latitude: p.latitude, longitude: p.longitude })))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- restore once per template
  }, [template])

  const aoi = wizard.aoi_geometry
  const draftArea = draw.points.length >= 3 ? polygonAreaM2(draw.points) : null
  const locked = readOnly || !wizard.editable
  const worldReady = session && ['world_ready'].includes(session.state)
  const preflight = session?.preflight
  const set = (k: keyof typeof form, v: unknown) => setForm((f) => ({ ...f, [k]: v }))
  const num = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => set(k, Number(e.target.value))

  const saveAoi = async () => {
    if (draw.points.length < 3) return
    await props.onSaveStep(2, { source: draw.mode === 'rectangle' ? 'rectangle' : 'drawn', geometry: { type: 'Polygon', coordinates: [closedRing(draw.points)] } })
    draw.setPoints([])
    draw.setDrawing(false)
  }

  const planMission = async () => {
    const pattern = template === 'search' ? 'sar_cells' : template === 'mapping' ? 'crosshatch' : 'lawnmower'
    const f: Partial<FlightParams> = {
      ...flight,
      altitude_m: form.altitude_m,
      speed_mps: form.speed_mps,
      overlap_pct: form.overlap_pct,
      pattern,
      rth_battery_pct: form.rth_battery_pct,
      rth_on_complete: form.rth_on_complete,
      turn_behaviour: form.turn_behaviour as FlightParams['turn_behaviour'],
      lane_spacing_m: template === 'agriculture' ? form.lane_spacing_m : null,
      sar_cell_size_m: template === 'search' ? form.sar_cell_size_m : null,
    }
    if (!(await props.onSaveStep(3, f))) return
    const c: Record<string, unknown> = {
      ...constraints,
      min_battery_pct: form.min_battery_pct,
      max_flight_time_min: form.max_flight_time_min || null,
      link_loss_policy: form.link_loss_policy,
      max_tasks_per_drone: 60,
      accept_unassigned: true,
      inspection_points: template === 'inspection' ? picked.map((p) => ({ ...p, hover_time_s: 10, priority: 3 })) : [],
      custom_waypoints: template === 'custom' ? picked : [],
    }
    if (!(await props.onSaveStep(5, c))) return
    await props.onAllocate()
  }

  return (
    <div style={{ display: 'grid', gap: 'var(--dm-space-sm)' }} data-testid="sim-setup">
      <Section n={1} title="Mission task" done={wizard.completed_steps.includes(1)} testId="sim-section-task">
        <select className="dm-input" value={template} disabled={locked || busy !== null} data-testid="sim-task-type"
          onChange={(e) => props.onSaveStep(1, { template: e.target.value })}>
          {SIM_TASK_TYPES.map((t) => (
            <option key={t.key} value={t.key}>{t.label}</option>
          ))}
        </select>
        <span style={{ fontSize: '.72rem', opacity: 0.75 }}>{SIM_TASK_TYPES.find((t) => t.key === template)?.hint}</span>
      </Section>

      <Section n={2} title="Area of interest" done={!!aoi} testId="sim-section-aoi">
        <div style={{ display: 'flex', gap: '.4rem', flexWrap: 'wrap' }}>
          {(['polygon', 'rectangle'] as DrawMode[]).map((m) => (
            <button key={m} type="button" className={`dm-btn ${draw.mode === m ? 'dm-btn--primary' : 'dm-btn--ghost'}`} disabled={locked}
              onClick={() => { draw.setMode(m); draw.setPoints([]); draw.setDrawing(true) }} data-testid={`sim-draw-${m}`}>
              {m}
            </button>
          ))}
          <button type="button" className="dm-btn dm-btn--primary" disabled={locked || draw.points.length < 3 || busy !== null} onClick={saveAoi} data-testid="sim-save-aoi">Save AOI</button>
          <button type="button" className="dm-btn dm-btn--ghost" disabled={locked} onClick={() => { draw.setPoints([]); draw.setDrawing(false) }} data-testid="sim-clear-aoi">Clear</button>
        </div>
        <select className="dm-input" disabled={locked || busy !== null} value="" data-testid="sim-existing-aoi"
          onChange={(e) => e.target.value && props.onSaveStep(2, { source: 'copied', copy_from_mission_id: e.target.value })}>
          <option value="">Use a saved AOI…</option>
          {missions.filter((m) => m.aoi_geometry && m.id !== wizard.mission_id).map((m) => (
            <option key={m.id} value={m.id}>{m.name} · {formatArea(m.aoi_area_sq_meters)}</option>
          ))}
        </select>
        <div className="dm-kv"><span>Area</span><strong className="dm-mono" data-testid="sim-aoi-area">
          {draftArea !== null ? `${(draftArea / 1e6).toFixed(3)} km² (draft)` : wizard.aoi_area_sq_meters ? `${(wizard.aoi_area_sq_meters / 1e6).toFixed(3)} km²` : '--'}
        </strong></div>
        {aoi && (
          <>
            <button type="button" className="dm-btn dm-btn--ghost" onClick={() => setShowGeo((v) => !v)} data-testid="sim-show-geojson">{showGeo ? 'Hide' : 'Show'} GeoJSON</button>
            {showGeo && (
              <pre className="dm-mono" style={{ fontSize: '.62rem', maxHeight: 160, overflow: 'auto', margin: 0, background: 'var(--dm-surface-inset)', padding: '.4rem' }} data-testid="sim-aoi-geojson">
                {JSON.stringify(aoi, null, 1)}
              </pre>
            )}
          </>
        )}
      </Section>

      <Section n={3} title="Drones" done={selectedDrones > 0} testId="sim-section-drones">
        <div style={{ display: 'flex', gap: '.4rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <select className="dm-input" value={count} disabled={locked || !aoi} onChange={(e) => setCount(Number(e.target.value))} data-testid="sim-drone-count">
            {DRONE_COUNTS.map((n) => (
              <option key={n} value={n} disabled={n > maxDrones}>{n} drone{n > 1 ? 's' : ''}{n > maxDrones ? ' (exceeds capacity)' : ''}</option>
            ))}
          </select>
          <button type="button" className="dm-btn dm-btn--primary" disabled={locked || !aoi || busy !== null} onClick={() => props.onStage(count)} data-testid="sim-stage-drones">
            <Plane size={13} /> Place drones
          </button>
        </div>
        <div className="dm-kv"><span>Available / selected</span><strong className="dm-mono" data-testid="sim-drone-availability">{fleetSize} sim · {selectedDrones} selected</strong></div>
        <div className="dm-kv"><span>Simulator capacity</span><strong className="dm-mono">{capacity?.available ? `${capacity.max_instances} SITL` : 'offline'}</strong></div>
      </Section>

      <Section n={4} title="Flight parameters" done={wizard.completed_steps.includes(5) && !!plan} testId="sim-section-flight">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.4rem' }}>
          <label className="dm-field"><span>Altitude m</span><input className="dm-input" type="number" min={10} max={120} value={form.altitude_m} onChange={num('altitude_m')} disabled={locked} data-testid="sim-altitude" /></label>
          <label className="dm-field"><span>Speed m/s</span><input className="dm-input" type="number" min={1} max={20} value={form.speed_mps} onChange={num('speed_mps')} disabled={locked} data-testid="sim-speed-mps" /></label>
          {template === 'agriculture' ? (
            <label className="dm-field"><span>Lane spacing m</span><input className="dm-input" type="number" min={5} max={200} value={form.lane_spacing_m} onChange={num('lane_spacing_m')} disabled={locked} /></label>
          ) : (
            <label className="dm-field"><span>Overlap %</span><input className="dm-input" type="number" min={0} max={90} value={form.overlap_pct} onChange={num('overlap_pct')} disabled={locked} data-testid="sim-overlap" /></label>
          )}
          {template === 'search' && (
            <label className="dm-field"><span>Cell size m</span><input className="dm-input" type="number" min={40} max={800} value={form.sar_cell_size_m} onChange={num('sar_cell_size_m')} disabled={locked} /></label>
          )}
          <label className="dm-field"><span>Turns</span>
            <select className="dm-input" value={form.turn_behaviour} onChange={(e) => set('turn_behaviour', e.target.value)} disabled={locked}>
              <option value="coordinated_turn">Coordinated</option><option value="stop_and_turn">Stop and turn</option>
            </select></label>
          <label className="dm-field"><span>RTH battery %</span><input className="dm-input" type="number" min={10} max={60} value={form.rth_battery_pct} onChange={num('rth_battery_pct')} disabled={locked} /></label>
          <label className="dm-field"><span>Min launch %</span><input className="dm-input" type="number" min={10} max={100} value={form.min_battery_pct} onChange={num('min_battery_pct')} disabled={locked} /></label>
          <label className="dm-field"><span>Max flight min</span><input className="dm-input" type="number" min={1} max={180} value={form.max_flight_time_min} onChange={num('max_flight_time_min')} disabled={locked} /></label>
          <label className="dm-field"><span>Link loss</span>
            <select className="dm-input" value={form.link_loss_policy} onChange={(e) => set('link_loss_policy', e.target.value)} disabled={locked}>
              {['RTH', 'HOLD', 'LAND', 'CONTINUE'].map((p) => <option key={p}>{p}</option>)}
            </select></label>
          <label className="dm-field"><span>Sim speed</span>
            <select className="dm-input" value={speed} onChange={(e) => setSpeed(Number(e.target.value) as SimSpeed)} data-testid="sim-initial-speed">
              {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
            </select></label>
          <label className="dm-field"><span>Terrain</span>
            <select className="dm-input" value={dem} onChange={(e) => setDem(e.target.value as 'terrarium' | 'flat')} data-testid="sim-terrain">
              <option value="terrarium">Real DEM (SRTM)</option><option value="flat">Flat (fallback)</option>
            </select></label>
        </div>
        <label className="dm-check"><input type="checkbox" checked={form.rth_on_complete} onChange={(e) => set('rth_on_complete', e.target.checked)} disabled={locked} /> Return to launch when done</label>
        {(template === 'inspection' || template === 'custom') && (
          <div style={{ display: 'grid', gap: '.3rem' }}>
            <button type="button" className={`dm-btn ${picking ? 'dm-btn--armed' : 'dm-btn--ghost'}`} onClick={() => setPicking(!picking)} disabled={locked} data-testid="sim-pick-targets">
              {picking ? 'Click the map… (done)' : template === 'custom' ? 'Pick waypoints on map' : 'Pick inspection targets on map'}
            </button>
            <span className="dm-mono" style={{ fontSize: '.7rem' }}>{picked.length} {template === 'custom' ? 'waypoints' : 'targets'}
              {picked.length > 0 && <button type="button" className="dm-btn dm-btn--ghost" style={{ marginLeft: 8 }} onClick={() => setPicked([])}>clear</button>}</span>
          </div>
        )}
        <button type="button" className="dm-btn dm-btn--primary" disabled={locked || !aoi || selectedDrones === 0 || busy !== null} onClick={planMission} data-testid="sim-generate-plan">
          Generate mission plan (CBBA)
        </button>
        {plan && (
          <div style={{ display: 'grid', gap: '.2rem' }} data-testid="sim-plan-summary">
            <div className="dm-kv"><span>Allocation</span><strong className="dm-mono" data-testid="sim-cbba-status">{plan.converged ? `CBBA converged · ${plan.iterations} it · ${plan.convergence_time_ms.toFixed(0)} ms` : plan.timed_out ? 'CBBA TIMED OUT' : 'CBBA NOT CONVERGED'}</strong></div>
            <div className="dm-kv"><span>Tasks / drones</span><strong className="dm-mono" data-testid="sim-plan-tasks">{plan.assigned_count}/{plan.task_count} · {plan.drone_count} drones</strong></div>
            <div className="dm-kv"><span>Unassigned</span><strong className="dm-mono">{plan.unassigned_count}</strong></div>
            <div className="dm-kv"><span>Est. duration / distance</span><strong className="dm-mono">{formatDuration(plan.estimated_duration_s)} · {formatDistance(plan.total_distance_m)}</strong></div>
            <div className="dm-kv"><span>Est. energy · coverage</span><strong className="dm-mono">{plan.estimated_energy_wh.toFixed(0)} Wh · {formatPct(plan.coverage_pct)}</strong></div>
            <table className="dm-data-table" data-testid="sim-assignments">
              <thead><tr><th>Drone</th><th>Tasks</th><th>ETA</th><th>Battery end</th></tr></thead>
              <tbody>{plan.bundles.map((b) => (
                <tr key={b.drone_id}><td>{b.drone_name}</td><td className="dm-mono">{b.task_count}</td><td className="dm-mono">{formatDuration(b.eta_seconds)}</td><td className="dm-mono">{formatPct(b.battery_end_pct)}</td></tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Section>

      <Section n={5} title="Simulation world" done={!!session?.world} testId="sim-section-world">
        <button type="button" className="dm-btn dm-btn--primary" disabled={!plan || plan.stale || busy !== null || (session !== null && !['created', 'world_ready', 'failed'].includes(session.state))}
          onClick={() => props.onGenerateWorld({ speed, dem })} data-testid="sim-generate-world">
          <Globe2 size={13} /> {session?.world ? 'Regenerate world' : 'Generate Gazebo world'}
        </button>
        {busy === 'world' && <span className="dm-mono" style={{ fontSize: '.72rem' }}>Fetching DEM tiles and building the heightmap…</span>}
        {session?.world && (
          <div style={{ display: 'grid', gap: '.15rem' }} data-testid="sim-world-meta">
            <div className="dm-kv"><span>World</span><strong className="dm-mono">{session.world.world_name}</strong></div>
            <div className="dm-kv"><span>Terrain</span><strong className="dm-mono" style={{ color: session.world.terrain.is_fallback ? '#FFD08A' : undefined }}>{session.world.terrain.is_fallback ? 'FALLBACK (flat)' : 'Real DEM'}</strong></div>
            <span style={{ fontSize: '.68rem', opacity: 0.8 }}>{session.world.terrain.source_label}</span>
            <div className="dm-kv"><span>Heightmap</span><strong className="dm-mono">{session.world.terrain.heightmap_px[0]} px · {session.world.terrain.heightmap_spacing_m} m</strong></div>
            <div className="dm-kv"><span>Elevation</span><strong className="dm-mono">{session.world.terrain.elevation_msl_min_m}–{session.world.terrain.elevation_msl_max_m} m MSL</strong></div>
            <div className="dm-kv"><span>AOI · extent</span><strong className="dm-mono">{session.world.aoi_area_km2} km² · {session.world.extent_m.side} m</strong></div>
            <div className="dm-kv"><span>Generated in</span><strong className="dm-mono" data-testid="sim-world-time">{session.world.timings_s.total_s.toFixed(2)} s</strong></div>
            <div className="dm-kv"><span>Origin</span><strong className="dm-mono" style={{ fontSize: '.68rem' }}>{session.world.frame.origin.latitude.toFixed(6)}, {session.world.frame.origin.longitude.toFixed(6)}</strong></div>
          </div>
        )}
      </Section>

      <Section n={6} title="Preflight & launch" done={!!preflight?.passed} testId="sim-section-preflight">
        <button type="button" className="dm-btn" disabled={!worldReady || busy !== null} onClick={props.onPreflight} data-testid="sim-run-preflight">
          <Cpu size={13} /> Run preflight checklist
        </button>
        {preflight && (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: '.15rem' }} data-testid="sim-preflight">
            {preflight.checks.map((c: PreflightCheck) => (
              <li key={c.code} className="dm-check" data-testid={`preflight-${c.code}`} data-passed={c.passed}>
                {c.passed ? <CheckCircle2 size={13} color="var(--dm-lane-2)" /> : <XCircle size={13} color="#FF8A80" />}
                <span><strong className="dm-mono" style={{ fontSize: '.68rem' }}>{c.code}</strong> {c.message}</span>
              </li>
            ))}
          </ul>
        )}
        <button type="button" className="dm-btn dm-btn--armed" disabled={!worldReady || !preflight?.passed || busy !== null} onClick={props.onStart} data-testid="sim-start">
          <Play size={13} /> Start simulation
        </button>
      </Section>
    </div>
  )
}
