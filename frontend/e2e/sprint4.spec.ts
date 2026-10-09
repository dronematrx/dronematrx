/**
 * Sprint 4 end-to-end suite (real stack: FastAPI runtime, NATS JetStream,
 * TimescaleDB, React UI). Runs serially because tests 2/4/7/10 observe one
 * live run of the deterministic 8-drone demo scenario.
 *
 * Evidence screenshots  -> docs/sprint4/evidence/*.png
 * Measured E2E values   -> docs/sprint4/performance/e2e_results.json
 */
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import * as fs from 'node:fs'
import * as path from 'node:path'
import { API, login, resetFleet } from './helpers'

const EVIDENCE = path.resolve(process.cwd(), '../docs/sprint4/evidence') // cwd = frontend/
const PERF = path.resolve(process.cwd(), '../docs/sprint4/performance')
const results: Record<string, unknown> = {}
let token = ''
let demoStartedAt = 0
let messagesAtStart = 0

test.describe.configure({ mode: 'serial' })
test.setTimeout(180_000)

/** The app scrolls inside its content pane (fullPage cannot reach it): bring
 * the card that holds `testId` to the top of the viewport before a screenshot. */
async function scrollCardToTop(page: Page, testId: string) {
  await page.getByTestId(testId).evaluate((el) => (el.closest('.dm-card') ?? el).scrollIntoView({ block: 'start' }))
  await page.waitForTimeout(300)
}

async function shot(page: Page, name: string, fullPage = false) {
  fs.mkdirSync(EVIDENCE, { recursive: true })
  await page.screenshot({ path: path.join(EVIDENCE, name), fullPage })
}

function api(request: APIRequestContext) {
  const headers = () => ({ Authorization: `Bearer ${token}` })
  return {
    get: async (p: string) => {
      const r = await request.get(`${API}${p}`, { headers: headers() })
      expect(r.ok(), `${p} ${r.status()}`).toBeTruthy()
      return r.json()
    },
    post: async (p: string, data?: unknown) => {
      const r = await request.post(`${API}${p}`, { headers: headers(), data })
      expect(r.ok(), `${p} ${r.status()}`).toBeTruthy()
      return r.json()
    },
    del: (p: string) => request.delete(`${API}${p}`, { headers: headers() }),
  }
}

async function untilDemoT(seconds: number) {
  const wait = demoStartedAt + seconds * 1000 - Date.now()
  if (wait > 0) await new Promise((r) => setTimeout(r, wait))
}

test.beforeAll(async ({ request }) => resetFleet(request))

test.afterAll(() => {
  fs.mkdirSync(PERF, { recursive: true })
  fs.writeFileSync(path.join(PERF, 'e2e_results.json'), JSON.stringify({ generated_at: new Date().toISOString(), ...results }, null, 2))
})

test('00 baseline: Drone MatrX running (Sprint 1-3 features intact)', async ({ page }) => {
  await login(page)
  token = (await page.evaluate(() => localStorage.getItem('dronematrx.access_token'))) ?? ''
  const health = await api(page.request).get('/health')
  expect(health.nats_connected).toBe(true)
  expect(health.orca_enabled).toBe(true)
  await page.goto('/missions')
  await expect(page.getByTestId('mission-row').first()).toBeVisible()
  await shot(page, '01_baseline_application.png')
  messagesAtStart = (await api(page.request).get('/messaging/status')).bus.messages
})

test('TEST 1: ORCA two-drone collision avoided', async ({ page }) => {
  const res = await api(page.request).post('/orca/simulate', {
    drones: [
      { drone_id: 'A', x: -80, y: 0, goal_x: 80, goal_y: 0, cruise_speed: 10 },
      { drone_id: 'B', x: 80, y: 0.5, goal_x: -80, goal_y: 0.5, cruise_speed: 10 },
    ],
  })
  const s = res.summary
  expect(s.predicted_conflicts).toBeGreaterThanOrEqual(1)
  expect(s.avoidance_events).toBeGreaterThanOrEqual(1)
  expect(s.collision_violations).toBe(0)
  expect(s.min_separation_m).toBeGreaterThanOrEqual(5.95)
  expect(s.arrived).toBe(2)
  results.test1_two_drone = s
})

test('TEST 3: 50 consecutive 8-drone ORCA runs, zero collisions', async ({ page }) => {
  const client = api(page.request)
  let violations = 0
  let minSep = Infinity
  let maxResolution = 0
  let arrived = 0
  for (let run = 0; run < 50; run++) {
    const { summary } = await client.post('/orca/simulate', { scenario: `run:${run}`, max_time_s: 90, record_every: 0 })
    expect(summary.agents).toBe(8)
    violations += summary.collision_violations
    minSep = Math.min(minSep, summary.min_separation_m)
    maxResolution = Math.max(maxResolution, summary.resolution_ms_max)
    arrived += summary.arrived
  }
  expect(violations).toBe(0)
  expect(maxResolution).toBeLessThan(500)
  results.test3_fifty_runs = { runs: 50, drone_runs: 400, collision_violations: violations, min_separation_m: minSep, max_resolution_ms: maxResolution, arrived }
})

test('TEST 2 + TEST 4: 8-drone demo, prediction alert >= 3 s ahead, avoidance, timeline', async ({ page }) => {
  await login(page)
  await page.goto('/fleet')
  await expect(page.getByTestId('fleet-wall')).toBeVisible()
  await expect(page.getByTestId('active-link-DMX-003')).toContainText('Wi-Fi')
  await shot(page, '09_communication_wifi.png')

  await page.goto('/map')
  // Record, inside the page, the exact moment the amber alert first renders
  // and the TTC it shows (independent of what the test is doing meanwhile).
  await page.evaluate(() => {
    const w = window as unknown as { __firstAlert?: { at: number; text: string } }
    const observer = new MutationObserver(() => {
      const el = document.querySelector('[data-testid="orca-prediction"]')
      if (el && !w.__firstAlert) {
        w.__firstAlert = { at: Date.now(), text: el.textContent ?? '' }
        observer.disconnect()
      }
    })
    observer.observe(document.body, { childList: true, subtree: true, characterData: true })
  })
  await page.getByTestId('demo-start').click()
  demoStartedAt = Date.now()
  await expect(page.getByTestId('demo-elapsed')).toBeVisible()
  await untilDemoT(7)
  await expect(page.locator('path.dm-orca-velocity').first()).toBeVisible()
  await shot(page, '02_orca_normal.png')

  // Amber alert: first appearance, and the TTC it shows at that moment.
  const alert = page.getByTestId('orca-prediction').first()
  await expect(alert).toBeVisible({ timeout: 20_000 })
  const first = await page.evaluate(() => (window as unknown as { __firstAlert?: { at: number; text: string } }).__firstAlert)
  expect(first).toBeTruthy()
  const alertSeenAt = first!.at
  const ttcAtAlert = Number(/TTC ([\d.]+) s/.exec(first!.text)?.[1])
  expect(ttcAtAlert).toBeGreaterThanOrEqual(3) // alert lead time before the predicted intersection
  await expect(page.locator('path.dm-orca-predict').first()).toBeVisible()
  await shot(page, '03_orca_collision_prediction.png')

  // Avoidance vectors once the conflicts enter ORCA's 5 s horizon.
  await expect(page.locator('path.dm-orca-avoidance').first()).toBeVisible({ timeout: 15_000 })
  await page.waitForTimeout(1500) // let the deviation from the planned track become visible
  await shot(page, '04_orca_avoidance.png')

  // Conflicts clear (both pairs) -> timeline shows the whole sequence.
  await expect(page.getByTestId('timeline-conflict_cleared')).toHaveCount(2, { timeout: 20_000 })
  const events = await api(page.request).get('/orca/events?limit=500')
  const since = events.filter((e: { wall_time: string }) => new Date(e.wall_time).getTime() >= demoStartedAt - 1000)
  const predicted = since.filter((e: { kind: string }) => e.kind === 'conflict_predicted')
  const cleared = since.filter((e: { kind: string }) => e.kind === 'conflict_cleared')
  expect(predicted.length).toBeGreaterThanOrEqual(2)
  for (const e of predicted) expect(e.details.time_to_collision_s).toBeGreaterThanOrEqual(3)
  for (const e of cleared) expect(e.details.min_separation_m).toBeGreaterThanOrEqual(5.9)
  expect(since.some((e: { kind: string }) => e.kind === 'avoidance_applied')).toBe(true)
  await page.getByTestId('orca-timeline').scrollIntoViewIfNeeded()
  await shot(page, '05_orca_timeline.png')

  await page.getByTestId('fit-all').click()
  await page.waitForTimeout(800)
  const state = await api(page.request).get('/orca/state')
  expect(state.drones.filter((d: { name: string }) => /^DMX-00[1-8]$/.test(d.name)).length).toBe(8)
  expect(state.timing.measured_hz).toBeGreaterThan(9.5)
  await shot(page, '06_orca_8_drone.png')

  results.test2_test4_orca_demo = {
    alert_first_seen_ttc_s: ttcAtAlert,
    alert_seen_at_demo_t_s: (alertSeenAt - demoStartedAt) / 1000,
    predictions: predicted.map((e: { drones: string[]; details: { time_to_collision_s: number } }) => e.details.time_to_collision_s),
    cleared_min_separation_m: cleared.map((e: { details: { min_separation_m: number } }) => e.details.min_separation_m),
    orca_timing: state.timing,
  }
})

test('TEST 7: communication fallback Wi-Fi -> LTE -> LoRa -> recovery (per-drone UI)', async ({ page }) => {
  await login(page)
  await page.goto('/fleet')
  const pill = page.getByTestId('active-link-DMX-003')
  await untilDemoT(19)
  await expect(pill).toContainText('LTE', { timeout: 6_000 })
  await expect(page.getByTestId('active-link-DMX-001')).toContainText('Wi-Fi') // per drone, not fleet-wide
  await shot(page, '10_communication_lte.png')

  await untilDemoT(29)
  await expect(pill).toContainText('LoRa', { timeout: 6_000 })
  await shot(page, '11_communication_lora.png')
  await page.getByTestId('table-link-DMX-003').scrollIntoViewIfNeeded()
  await expect(page.getByTestId('table-link-DMX-003')).toContainText('LoRa')
  await shot(page, '12_fleet_link_status.png')

  await page.goto('/map') // whole system mid-scenario: ORCA + comms + NATS
  await page.waitForTimeout(1500)
  await shot(page, '15_end_to_end.png', true)

  await page.goto('/fleet')
  await untilDemoT(39)
  await expect(page.getByTestId('active-link-DMX-003')).toContainText('Wi-Fi', { timeout: 10_000 }) // recovery after the hold
  const { switches } = await api(page.request).get('/comms/events')
  const recent = switches.filter((s: { name: string }) => s.name === 'DMX-003').slice(-3)
  const byTransition = Object.fromEntries(recent.map((s: { from: string; to: string }) => [`${s.from}->${s.to}`, s]))
  expect(byTransition['wifi->lte'].total_ms).toBeLessThan(1000)
  expect(byTransition['lte->lora'].total_ms).toBeLessThan(1000)
  expect(byTransition['lora->wifi']).toBeTruthy()
  results.test7_fallback = byTransition
})

test('TEST 5 + TEST 6: NATS telemetry flow and JetStream replay', async ({ page }) => {
  await login(page)
  await page.goto('/analytics')
  await page.getByTestId('tab-bus').click()
  await expect(page.getByTestId('nats-connected')).toContainText('CONNECTED')
  await expect(page.getByTestId('nats-recent').locator('tbody tr').first()).toBeVisible()
  await expect(page.getByTestId('nats-consumers')).toContainText('telemetry-writer')
  const status = await api(page.request).get('/messaging/status')
  expect(status.bus.messages).toBeGreaterThan(messagesAtStart)
  expect(status.orchestrator.over_50ms).toBe(0)
  await shot(page, '07_nats_messages.png')

  await page.getByTestId('nats-replay').click()
  await expect(page.getByTestId('nats-replay-result')).toContainText('Replayed')
  const replay = await api(page.request).post('/messaging/replay', { kind: 'telemetry', minutes: 5, limit: 2000 })
  expect(replay.count).toBeGreaterThan(100)
  const seqs = replay.messages.map((m: { stream_seq: number }) => m.stream_seq)
  expect([...seqs].sort((a, b) => a - b)).toEqual(seqs) // ordered
  await scrollCardToTop(page, 'nats-replay-result')
  await shot(page, '08_nats_replay.png')
  results.test5_test6_nats = { stream_messages: status.bus.messages, delivery: status.orchestrator, replayed: replay.count }
})

test('TEST 9: 1-hour TimescaleDB query < 500 ms (UI)', async ({ page }) => {
  await login(page)
  await page.goto('/analytics')
  await page.getByTestId('ts-run-query').click()
  await expect(page.getByTestId('ts-records').locator('tbody tr').first()).toBeVisible()
  const droneMs = Number(((await page.getByTestId('ts-drone-query-ms').locator('strong').textContent()) ?? '').replace(' ms', ''))
  const fleetMs = Number(((await page.getByTestId('ts-fleet-query-ms').locator('strong').textContent()) ?? '').replace(' ms', ''))
  expect(droneMs).toBeLessThan(500)
  expect(fleetMs).toBeLessThan(500)
  await scrollCardToTop(page, 'ts-records')
  await shot(page, '13_timescaledb_telemetry.png')
  results.test9_query = { drone_query_ms: droneMs, fleet_query_ms: fleetMs }
})

test('TEST 8: 16-drone live telemetry ingestion into TimescaleDB', async ({ page }) => {
  await login(page)
  const client = api(page.request)
  const created: string[] = []
  for (let n = 1; n <= 8; n++) {
    const d = await client.post('/drones', {
      name: `E2E16-${String(n).padStart(2, '0')}`, model: 'M30', uin: `E2E16-${Date.now()}-${n}`,
      latitude: 23.2156 - 0.002 + n * 0.0004, longitude: 72.6369 + 0.003,
    })
    created.push(d.id)
  }
  try {
    await page.waitForTimeout(22_000)
    const stats = await client.get('/telemetry/stats?minutes=1')
    const reporting = stats.per_drone.filter((d: { rows: number }) => d.rows >= 15)
    expect(reporting.length).toBeGreaterThanOrEqual(16)
    expect(stats.ingestor.rejected).toBe(0)
    expect(stats.ingestor.write_errors).toBe(0)
    await page.goto('/analytics')
    await page.getByLabel('Window minutes').selectOption('1')
    await expect(page.getByTestId('ts-drones').locator('strong')).toHaveText(/^(1[6-9]|[2-9]\d)$/)
    await shot(page, '14_timescaledb_16_drones.png', true)
    results.test8_sixteen_drones = { drones_reporting: reporting.length, per_drone_rows_last_minute: Object.fromEntries(stats.per_drone.map((d: { name: string; rows: number }) => [d.name, d.rows])), ingestor: stats.ingestor }
  } finally {
    for (const id of created) await client.del(`/drones/${id}`)
  }
})

test('TEST 10: complete Sprint 4 flow verified end to end', async ({ page }) => {
  await login(page)
  const client = api(page.request)
  const drones = await client.get('/drones')
  const d3 = drones.items.find((d: { name: string }) => d.name === 'DMX-003')
  // Window = this run's scenario only (earlier rows may predate a server restart).
  const since = encodeURIComponent(new Date(demoStartedAt).toISOString())
  const window = await client.get(`/telemetry/drones/${d3.id}?start=${since}`)
  const links = new Set(window.records.map((r: { link_type: string }) => r.link_type))
  // Telemetry kept flowing over every transport the drone fell back to.
  expect([...links]).toEqual(expect.arrayContaining(['wifi', 'lte', 'lora']))
  const gaps = window.records.slice(1).map((r: { timestamp: string }, i: number) => new Date(r.timestamp).getTime() - new Date(window.records[i].timestamp).getTime())
  expect(Math.max(...gaps)).toBeLessThan(2500) // ~1 Hz with no outage through the fallbacks
  const status = await client.get('/messaging/status')
  expect(status.orchestrator.by_kind.state).toBeGreaterThan(0)
  expect(status.orchestrator.by_kind.alert).toBeGreaterThan(0)
  results.test10_end_to_end = { dmx003_links_seen: [...links], max_gap_ms: Math.max(...gaps), rows_since_demo_start: window.rows, message_kinds: status.orchestrator.by_kind }
})
