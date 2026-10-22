/**
 * Sprint 5 end-to-end: AOI on Leaflet -> CBBA -> Gazebo world -> PX4 SITL
 * fleet -> live telemetry on the same map -> controls / failure -> report.
 *
 * Needs the full stack: `docker compose -f docker-compose.yml -f
 * docker-compose.sim.yml up -d` (PostGIS, NATS, simhost) plus backend +
 * Vite (started by playwright.config.ts if not running).
 *
 * Evidence:  documents/sprint5/screenshots/*.png
 *            documents/sprint5/recordings/*.webm (browser video)
 * Results:   docs/sprint5/performance/e2e_results.json
 */
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import * as fs from 'node:fs'
import * as path from 'node:path'
import { API, login } from './helpers'

const DOCS = path.resolve(process.cwd(), '../documents/sprint5')
const SHOTS = path.join(DOCS, 'screenshots')
const RECORDINGS = path.join(DOCS, 'recordings')
const PERF = path.resolve(process.cwd(), '../docs/sprint5/performance')
const SIMHOST = 'http://localhost:8100'
const results: Record<string, unknown> = {}

// Deterministic test AOI: ~260 m x 260 m of open ground east of Gandhinagar.
const AOI = { lat: 23.24, lng: 72.65, half: 0.0012 }
const DRONES = 3

test.describe.configure({ mode: 'serial' })
test.setTimeout(30 * 60_000)
test.use({ video: { mode: 'on', size: { width: 1600, height: 1000 } }, viewport: { width: 1600, height: 1000 } })

let token = ''
let missionId = ''
let sessionId = ''

async function shot(page: Page, name: string) {
  fs.mkdirSync(SHOTS, { recursive: true })
  await page.waitForTimeout(400)
  await page.screenshot({ path: path.join(SHOTS, name) })
}

function api(request: APIRequestContext) {
  const h = () => ({ Authorization: `Bearer ${token}` })
  return {
    get: async (p: string) => {
      // Fresh connection per call: a pooled keep-alive socket can be closed by
      // uvicorn (5 s idle) exactly when a 5 s poll reuses it.
      for (let attempt = 0; ; attempt++) {
        try {
          const r = await request.get(`${API}${p}`, { headers: { ...h(), Connection: 'close' } })
          expect(r.ok(), `${p} ${r.status()}`).toBeTruthy()
          return r.json()
        } catch (err) {
          if (attempt >= 2 || !String(err).includes('socket hang up')) throw err
        }
      }
    },
    post: async (p: string, data?: unknown) => {
      const r = await request.post(`${API}${p}`, { headers: { ...h(), Connection: 'close' }, data })
      expect(r.ok(), `${p} ${r.status()} ${await r.text()}`).toBeTruthy()
      return r.json()
    },
  }
}

async function clickSimMap(page: Page, lat: number, lng: number) {
  const box = await page.locator('[data-testid="sim-map"] .leaflet-container').boundingBox()
  if (!box) throw new Error('map not visible')
  const pt = await page.evaluate(([la, ln]) => {
    const map = (window as unknown as { __dmMap: { latLngToContainerPoint: (p: [number, number]) => { x: number; y: number } } }).__dmMap
    return map.latLngToContainerPoint([la, ln])
  }, [lat, lng] as [number, number])
  await page.mouse.click(box.x + pt.x, box.y + pt.y)
}

/** Haversine distance in metres. */
function dist(a: [number, number], b: [number, number]) {
  const R = 6371008.8
  const r = Math.PI / 180
  const dLat = (b[0] - a[0]) * r
  const dLon = (b[1] - a[1]) * r
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(h))
}

async function markerPositions(page: Page) {
  return page.$$eval('[data-testid^="sim-marker-"]', (els) =>
    els.map((e) => ({ uin: e.getAttribute('data-testid')!.replace('sim-marker-', ''), id: e.getAttribute('data-drone-id')!, lat: Number(e.getAttribute('data-lat')), lon: Number(e.getAttribute('data-lon')), state: e.getAttribute('data-state'), stale: e.getAttribute('data-stale') })),
  )
}

/** GET a simhost endpoint; retries once if a pooled keep-alive socket was
 * closed by the server between calls ("socket hang up"). */
async function simGet(request: APIRequestContext, p: string) {
  for (let attempt = 0; ; attempt++) {
    try {
      const r = await request.get(`${SIMHOST}${p}`, { headers: { Connection: 'close' } })
      return r.json()
    } catch (err) {
      if (attempt >= 2) throw err
    }
  }
}

test.beforeAll(async ({ request }) => {
  const r = await request.post(`${API}/auth/login`, { data: { email: 'operator@dronematrx.com', password: 'DroneMatrx@2026' } })
  token = (await r.json()).access_token
  const h = await request.get(`${SIMHOST}/health`)
  expect(h.ok(), 'simhost must be running (docker-compose.sim.yml)').toBeTruthy()
  results.simhost = await h.json()
})

test.afterAll(() => {
  fs.mkdirSync(PERF, { recursive: true })
  fs.writeFileSync(path.join(PERF, 'e2e_results.json'), JSON.stringify(results, null, 2))
})

test('AOI selected on Leaflet -> Gazebo SITL simulation -> live map -> report', async ({ page, request }) => {
  const a = api(request)
  await login(page)

  // 1. New simulation mission
  await page.goto('/missions')
  await page.getByTestId('new-mission-name').fill(`E2E Sim ${Date.now()}`)
  await page.getByTestId('create-simulation').click()
  await expect(page).toHaveURL(/\/missions\/[0-9a-f-]+\/simulate/)
  missionId = page.url().split('/missions/')[1].split('/')[0]
  await expect(page.getByTestId('simulation-badge')).toBeVisible()

  // 2. Task + AOI (rectangle drawn on the real Leaflet map)
  await page.getByTestId('sim-task-type').selectOption('survey')
  await expect.poll(async () => (await a.get(`/missions/${missionId}/wizard`)).completed_steps).toContain(1)
  await page.getByTestId('sim-draw-rectangle').click()
  await clickSimMap(page, AOI.lat - AOI.half, AOI.lng - AOI.half)
  await clickSimMap(page, AOI.lat + AOI.half, AOI.lng + AOI.half)
  await page.getByTestId('sim-save-aoi').click()
  await expect.poll(async () => (await a.get(`/missions/${missionId}/wizard`)).aoi_geometry).not.toBeNull()
  const wizard = await a.get(`/missions/${missionId}/wizard`)
  const ring: number[][] = wizard.aoi_geometry.coordinates[0]
  expect(ring.length).toBe(5)
  for (const [lon, lat] of ring) {
    expect(Math.abs(lat - AOI.lat)).toBeLessThan(AOI.half * 1.05)
    expect(Math.abs(lon - AOI.lng)).toBeLessThan(AOI.half * 1.05)
  }
  results.aoi = { geojson: wizard.aoi_geometry, area_km2: wizard.aoi_area_sq_meters / 1e6 }

  // 3. Drones
  await page.getByTestId('sim-drone-count').selectOption(String(DRONES))
  await page.getByTestId('sim-stage-drones').click()
  await expect(page.getByTestId('sim-drone-availability')).toContainText(`${DRONES} selected`)
  await shot(page, '01_simulation_setup.png')
  await page.getByTestId('sim-show-geojson').click()
  await expect(page.getByTestId('sim-aoi-geojson')).toContainText('Polygon')
  await shot(page, '02_aoi_geojson.png')

  // 4. Flight parameters + CBBA
  await page.getByTestId('sim-altitude').fill('30')
  await page.getByTestId('sim-speed-mps').fill('8')
  await page.getByTestId('sim-overlap').fill('50')
  await page.getByTestId('sim-generate-plan').click()
  await expect(page.getByTestId('sim-cbba-status')).toContainText('converged', { timeout: 30_000 })
  const plan = await a.get(`/missions/${missionId}/cbba`)
  expect(plan.converged).toBeTruthy()
  expect(plan.bundles.length).toBeGreaterThan(0)
  results.cbba = { tasks: plan.task_count, assigned: plan.assigned_count, drones: plan.drone_count, iterations: plan.iterations, convergence_ms: plan.convergence_time_ms }
  await page.getByTestId('sim-plan-summary').scrollIntoViewIfNeeded()
  await shot(page, '03_cbba_allocation.png')

  // 5. World from the selected AOI (real DEM)
  await page.getByTestId('sim-terrain').selectOption('terrarium')
  const tWorld = Date.now()
  await page.getByTestId('sim-generate-world').click()
  await expect(page.getByTestId('sim-world-meta')).toBeVisible({ timeout: 120_000 })
  const sessions = await a.get(`/simulations?mission_id=${missionId}`)
  sessionId = sessions[0].id
  const s1 = await a.get(`/simulations/${sessionId}`)
  expect(s1.state).toBe('world_ready')
  expect(s1.world.frame.origin.latitude).toBeCloseTo(AOI.lat, 3)
  expect(s1.world.frame.origin.longitude).toBeCloseTo(AOI.lng, 3)
  results.world = { ui_wait_s: (Date.now() - tWorld) / 1000, ...s1.world }
  await page.getByTestId('sim-world-meta').scrollIntoViewIfNeeded()
  await shot(page, '04_world_generation.png')

  // 6. Preflight + start
  await page.getByTestId('sim-run-preflight').click()
  await expect(page.getByTestId('sim-preflight')).toBeVisible()
  await expect(page.getByTestId('sim-start')).toBeEnabled({ timeout: 30_000 })
  const tStart = Date.now()
  await page.getByTestId('sim-start').click()
  await expect(page.getByTestId('sim-controls')).toBeVisible()
  await expect.poll(async () => (await a.get(`/simulations/${sessionId}`)).state, { timeout: 8 * 60_000, intervals: [3000] }).toBe('running')
  results.startup_s = (Date.now() - tStart) / 1000

  // 7. Exactly N simulator drones, bound to this mission
  const run = await a.get(`/simulations/${sessionId}`)
  expect(run.spawned_drones).toBe(DRONES)
  expect(run.instances.length).toBe(DRONES)
  const procs = await simGet(request, `/processes`)
  const mine = procs.px4.filter((p: { session: string }) => p.session === sessionId)
  expect(mine.length).toBe(DRONES)
  results.sitl = { pids: mine.map((p: { pid: number }) => p.pid), instances: run.instances.map((i: { uin: string; mavlink_port: number; gz_model_name: string }) => ({ uin: i.uin, port: i.mavlink_port, model: i.gz_model_name })) }

  // 8. Initial markers sit on the launch pads (spawn -> Gazebo -> PX4 -> NATS -> map)
  await expect.poll(async () => (await markerPositions(page)).length, { timeout: 60_000 }).toBe(DRONES)
  const first = await markerPositions(page)
  const spawnErr = first.map((m) => {
    const inst = run.instances.find((i: { uin: string }) => i.uin === m.uin)
    return dist([m.lat, m.lon], [inst.spawn.latitude, inst.spawn.longitude])
  })
  results.initial_marker_vs_spawn_m = spawnErr
  for (const e of spawnErr) expect(e).toBeLessThan(5)
  await shot(page, '07_simulation_running.png')

  // Gazebo itself: GUI on the simulator's virtual display, camera on the launch pads.
  const gz = async (p: string, data?: unknown) => {
    const r = await request.post(`${SIMHOST}/sessions/${sessionId}${p}`, { data, timeout: 180_000, headers: { Connection: 'close' } })
    expect(r.ok(), `${p} ${r.status()} ${await r.text()}`).toBeTruthy()
    return r.json()
  }
  await gz('/gui/start')
  // Camera ~9 m from the first drone's live Gazebo ground-truth pose, looking at it.
  const truthNow = async () => (await simGet(request, `/sessions/${sessionId}`)).drones[run.instances[0].uin].truth
  let t0 = await truthNow()
  await gz('/gui/camera', { x: t0.x - 6, y: t0.y - 6, z: t0.z + 2.5, look_at: [t0.x, t0.y, t0.z] })
  await page.waitForTimeout(2500)
  t0 = await truthNow()
  await gz('/gui/camera', { x: t0.x - 6, y: t0.y - 6, z: t0.z + 2.5, look_at: [t0.x, t0.y, t0.z] })
  await gz('/gui/screenshot', { filename: '05_gazebo_world.png' })
  await gz('/gui/follow', { model: run.instances[0].gz_model_name, offset: [-10, -10, 4] }) // chase cam for the recording
  await gz('/gui/record/start', { filename: 'sprint5_gazebo_run.mp4' })
  await page.getByTestId('sim-sitl-table').scrollIntoViewIfNeeded()
  await shot(page, '06_sitl_fleet.png')

  // 9. Telemetry comes from the simulation and moves the markers
  await expect.poll(async () => {
    const now = await markerPositions(page)
    return Math.max(...now.map((m) => dist([m.lat, m.lon], [first.find((f) => f.uin === m.uin)!.lat, first.find((f) => f.uin === m.uin)!.lon])))
  }, { timeout: 180_000, intervals: [2000] }).toBeGreaterThan(20)
  // Marker vs backend telemetry (same frame, <= 2 s apart at 8 m/s)
  const markers = await markerPositions(page)
  const live = (await a.get(`/simulations/${sessionId}`)).live
  const sync = markers.map((m) => {
    const d = live.drones.find((x: { uin: string }) => x.uin === m.uin)
    return d && d.lat != null ? dist([m.lat, m.lon], [d.lat, d.lon]) : null
  })
  results.marker_vs_backend_telemetry_m = sync
  for (const e of sync) if (e !== null) expect(e).toBeLessThan(25)
  const truthErr = live.drones.map((d: { stats: unknown; gps_error_m: number | null }) => d.gps_error_m)
  results.reported_vs_gazebo_truth_m = truthErr
  await shot(page, '08_leaflet_live_drones.png')

  // 10. Trajectory + task progress
  await expect.poll(async () => page.locator('path.dm-sim-track').count(), { timeout: 60_000 }).toBeGreaterThan(0)
  await expect.poll(async () => page.locator('path.dm-sim-planned').count()).toBeGreaterThan(0)
  await expect.poll(async () => (await a.get(`/simulations/${sessionId}`)).progress.tasks_completed, { timeout: 300_000, intervals: [3000] }).toBeGreaterThan(0)
  await shot(page, '09_leaflet_trajectory.png')
  await page.getByTestId('sim-drone-table').scrollIntoViewIfNeeded()
  await shot(page, '10_telemetry_updates.png')

  // 11. Pause -> no advancement; resume; speed
  await page.getByTestId('sim-pause').click()
  await expect(page.getByTestId('sim-resume')).toBeVisible()
  const p1 = await simGet(request, `/sessions/${sessionId}`)
  await page.waitForTimeout(3000)
  const p2 = await simGet(request, `/sessions/${sessionId}`)
  expect(Math.abs(p2.stats.sim_time_s - p1.stats.sim_time_s)).toBeLessThan(0.05)
  await shot(page, '11_simulation_controls.png')
  await page.getByTestId('sim-resume').click()
  await page.waitForTimeout(3000)
  const p3 = await simGet(request, `/sessions/${sessionId}`)
  expect(p3.stats.sim_time_s).toBeGreaterThan(p2.stats.sim_time_s + 1)
  await page.getByTestId('sim-speed-2').click()
  await expect.poll(async () => (await simGet(request, `/sessions/${sessionId}`)).requested_speed).toBe(2)
  results.pause = { before: p1.stats.sim_time_s, after_3s: p2.stats.sim_time_s, after_resume: p3.stats.sim_time_s }

  // 12. Controlled drone failure -> CBBA re-allocation
  await page.getByTestId('sim-failure-panel').scrollIntoViewIfNeeded()
  await shot(page, '12_failure_injection.png')
  const victim = run.instances[run.instances.length - 1]
  await page.getByTestId('sim-failure-drone').selectOption(victim.drone_id)
  await page.getByTestId('sim-kill').click()
  await expect(page.getByTestId('sim-event-process_crashed').first()).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('sim-event-tasks_reallocated').first()).toBeVisible({ timeout: 30_000 })
  const events = await a.get(`/simulations/${sessionId}/events`)
  const realloc = events.find((e: { event_type: string }) => e.event_type === 'tasks_reallocated')
  expect(['replanned', 'continued']).toContain(realloc.mission_outcome)
  results.failure = { realloc: realloc.response, details: realloc.details }
  await expect.poll(async () => (await markerPositions(page)).find((m) => m.uin === victim.uin)?.state).toBe('failed')
  await shot(page, '13_drone_failure_response.png')
  await page.waitForTimeout(20_000) // record the re-planned flight in Gazebo
  await gz('/gui/record/stop')
  const ws = path.resolve(process.cwd(), `../backend/sim_workspace/${sessionId}/evidence`)
  fs.mkdirSync(RECORDINGS, { recursive: true })
  fs.copyFileSync(path.join(ws, '05_gazebo_world.png'), path.join(SHOTS, '05_gazebo_world.png'))
  fs.copyFileSync(path.join(ws, 'sprint5_gazebo_run.mp4'), path.join(RECORDINGS, 'sprint5_gazebo_run.mp4'))

  // 13. Completion -> report
  await expect.poll(async () => (await a.get(`/simulations/${sessionId}`)).state, { timeout: 20 * 60_000, intervals: [5000] }).toMatch(/completed|aborted/)
  const done = await a.get(`/simulations/${sessionId}`)
  results.final = { state: done.state, progress: done.progress, sim_time_s: done.sim_time_s }
  const mission = await a.get(`/missions/${missionId}`)
  expect(['completed', 'aborted']).toContain(mission.status)
  await expect(page.getByTestId('sim-report')).toBeVisible({ timeout: 60_000 })
  const report = await a.get(`/simulations/${sessionId}/report`)
  expect(report.generation_ms).toBeLessThan(30_000)
  expect(report.data.mission_summary.drones_spawned).toBe(DRONES)
  expect(report.data.task_summary.completed).toBe(done.progress.tasks_completed)
  expect(report.data.failure_summary.drone_failures).toBe(1)
  results.report = { generation_ms: report.generation_ms, task_summary: report.data.task_summary, battery: report.data.battery }
  await expect(page.getByTestId('report-tasks')).toContainText(`${report.data.task_summary.completed}/`)
  await shot(page, '14_simulation_report.png')

  // 14. Cleanup: no PX4 process of this session left
  const after = await simGet(request, `/processes`)
  expect(after.px4.filter((p: { session: string }) => p.session === sessionId)).toEqual([])
  results.leftover_processes = 0
  await shot(page, '16_end_to_end_pass.png')
  results.total_s = (Date.now() - tStart) / 1000

  const video = page.video()
  await page.close()
  if (video) {
    fs.mkdirSync(RECORDINGS, { recursive: true })
    await video.saveAs(path.join(RECORDINGS, 'sprint5_e2e_simulation.webm'))
  }
})

test('report comparison against a re-run of the same mission', async ({ page, request }) => {
  const a = api(request)
  await login(page)
  // Re-run: same AOI, task type, fleet size; flown without injected failures and stopped after progress.
  const { mission_id } = await a.post(`/simulations/${sessionId}/rerun`)
  const s = await a.post('/simulations', { mission_id, speed: 2, dem_source: 'terrarium' })
  await a.post(`/simulations/${s.id}/world/generate`)
  const pf = await a.post(`/simulations/${s.id}/preflight`)
  expect(pf.passed, JSON.stringify(pf.checks)).toBeTruthy()
  await a.post(`/simulations/${s.id}/start`)
  await expect.poll(async () => (await a.get(`/simulations/${s.id}`)).state, { timeout: 8 * 60_000, intervals: [3000] }).toBe('running')
  await expect.poll(async () => (await a.get(`/simulations/${s.id}`)).progress.tasks_completed, { timeout: 600_000, intervals: [5000] }).toBeGreaterThan(2)
  await a.post(`/simulations/${s.id}/stop`)
  await page.goto(`/missions/${mission_id}/simulate`)
  await expect(page.getByTestId('sim-report')).toBeVisible({ timeout: 60_000 })
  await page.getByTestId('report-baseline').selectOption(sessionId)
  await page.getByTestId('report-compare-btn').click()
  await expect(page.getByTestId('report-diff')).toBeVisible()
  // Vertical scroll only (a horizontal nudge would crop the panel), then let map tiles load.
  await page.getByTestId('report-compare').evaluate((el) => {
    el.scrollIntoView({ block: 'start', inline: 'nearest' })
    for (let p = el.parentElement; p; p = p.parentElement) p.scrollLeft = 0
  })
  await page.waitForTimeout(2500)
  await shot(page, '15_report_comparison.png')
  const cmp = await a.post(`/simulations/${s.id}/report/compare`, { baseline_session_id: sessionId })
  results.comparison = cmp
  expect(cmp.diff.coverage_pct).toBeDefined()
  expect(cmp.diff.battery_used_total_pct).toBeDefined()
  // The re-run is stopped part-way; the diff must say its totals cover less of the mission.
  expect(cmp.warnings.join(' ')).toContain('Different outcome (stopped vs completed)')
})
