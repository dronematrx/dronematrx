/**
 * Sprint 6 full mission-control E2E on the real stack (see playwright.sprint6.config.ts):
 * backend + PostGIS + NATS, PX4 SITL bench in Gazebo (simhost), MediaMTX WebRTC.
 * No real aircraft: every flight controller here is PX4 SITL; the hardware adapter is locked.
 *
 * Evidence: docs/sprint6/evidence/NN_*.png (copied to documents/sprint6/screenshots/),
 *           documents/sprint6/recordings/ (browser video, Gazebo bench recording),
 *           docs/sprint6/performance/e2e_results.json (every measurement).
 */
import fs from 'node:fs'
import path from 'node:path'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { STATE_FILE, login } from './sprint6.setup'

const API = 'http://localhost:8000'
const SIMHOST = 'http://localhost:8100'
const EVIDENCE = path.resolve(process.cwd(), '../docs/sprint6/evidence')
const DOCS = path.resolve(process.cwd(), '../documents/sprint6')
const PERF = path.resolve(process.cwd(), '../docs/sprint6/performance')
const results: Record<string, unknown> = { steps: {} as Record<string, string> }
const steps = results.steps as Record<string, string>

test.describe.configure({ mode: 'serial' })

interface BenchState {
  bench: { session_id: string; controllers: string[]; markers: { name: string; latitude: number; longitude: number }[]; startup_s: number }
  benchWallS: number
  qr_text: string
}
const state = (): BenchState => JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'))

async function shot(page: Page, name: string) {
  fs.mkdirSync(EVIDENCE, { recursive: true })
  await page.screenshot({ path: path.join(EVIDENCE, name) })
}

/** Harness API client. Connection: close + one retry: a reused keep-alive socket
 * the server already closed fails with ECONNRESET (seen in Sprint 5 as well). */
async function retry<T>(fn: () => Promise<T>): Promise<T> {
  try {
    return await fn()
  } catch (err) {
    if (!/ECONNRESET|socket hang up|EPIPE/.test(String(err))) throw err
    await new Promise((r) => setTimeout(r, 300))
    return fn()
  }
}

function client(request: APIRequestContext, token: string) {
  const h = { Authorization: `Bearer ${token}`, Connection: 'close' }
  return {
    get: (p: string) => retry(async () => {
      const r = await request.get(`${API}${p}`, { headers: h })
      return { status: r.status(), body: await r.json().catch(() => null) }
    }),
    post: (p: string, data?: unknown) => retry(async () => {
      const r = await request.post(`${API}${p}`, { headers: h, data })
      return { status: r.status(), body: await r.json().catch(() => null) }
    }),
    del: (p: string) => retry(async () => (await request.delete(`${API}${p}`, { headers: h })).status()),
  }
}

async function sim(request: APIRequestContext, method: 'GET' | 'POST', p: string, data?: unknown) {
  return retry(async () => {
    const headers = { Connection: 'close' }
    const r = method === 'GET' ? await request.get(`${SIMHOST}${p}`, { headers }) : await request.post(`${SIMHOST}${p}`, { data, headers })
    return r.json()
  })
}

async function uiLogin(page: Page, email: string) {
  await page.goto('/login')
  await page.getByLabel(/email/i).fill(email)
  await page.getByLabel(/password/i).fill('DroneMatrx@2026')
  await page.getByRole('button', { name: /log in|sign in/i }).click()
  await page.waitForURL(/\/(missions|map|fleet|mission-control)/)
}

async function clickMap(page: Page, lat: number, lon: number) {
  const box = await page.locator('[data-testid="mc-map"] .leaflet-container').boundingBox()
  const pt = await page.evaluate(([la, lo]) => {
    const map = (window as unknown as { __dmMap: { latLngToContainerPoint: (p: [number, number]) => { x: number; y: number }; setView: (p: [number, number], z: number) => void } }).__dmMap
    return map.latLngToContainerPoint([la, lo])
  }, [lat, lon])
  await page.mouse.click(box!.x + pt.x, box!.y + pt.y)
}

async function centre(page: Page, lat: number, lon: number, zoom = 18) {
  await page.evaluate(([la, lo, z]) => { (window as unknown as { __dmMap: { setView: (p: [number, number], z: number) => void } }).__dmMap.setView([la, lo], z) }, [lat, lon, zoom])
  await page.waitForTimeout(800)
}

const droneRow = (page: Page, name: string) => page.getByTestId(`mc-drone-${name}`)
/** Select a drone in the fleet list. The list re-renders every second (ages), which can keep
 * Playwright's "stable" check from settling, so click without it and assert the selection. */
async function selectRow(page: Page, name: string) {
  await droneRow(page, name).scrollIntoViewIfNeeded()
  await droneRow(page, name).click({ force: true })
  await expect(droneRow(page, name)).toHaveAttribute('aria-selected', 'true')
}

async function waitCommand(api: ReturnType<typeof client>, id: string, states: string[], timeoutMs = 60_000) {
  const t0 = Date.now()
  for (;;) {
    const rows = (await api.get('/mission-control/commands?limit=200')).body as { id: string; state: string; timings: Record<string, number> }[]
    const row = rows.find((r) => r.id === id)
    if (row && states.includes(row.state)) return row
    if (Date.now() - t0 > timeoutMs) throw new Error(`command ${id} stuck in ${row?.state}`)
    await new Promise((r) => setTimeout(r, 800))
  }
}

function pct(values: number[], p: number) {
  const s = [...values].sort((a, b) => a - b)
  return s.length ? s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))] : null
}
function stats(values: number[]) {
  return { n: values.length, median: pct(values, 50), p95: pct(values, 95), max: values.length ? Math.max(...values) : null, min: values.length ? Math.min(...values) : null }
}

test.afterAll(() => {
  fs.mkdirSync(PERF, { recursive: true })
  fs.writeFileSync(path.join(PERF, 'e2e_results.json'), JSON.stringify(results, null, 2))
  fs.mkdirSync(path.join(DOCS, 'screenshots'), { recursive: true })
  if (fs.existsSync(EVIDENCE)) for (const f of fs.readdirSync(EVIDENCE)) fs.copyFileSync(path.join(EVIDENCE, f), path.join(DOCS, 'screenshots', f))
})

test('Sprint 6 mission control end to end (SITL bench, WebRTC, detections)', async ({ page, request, browser }, testInfo) => {
  const st = state()
  const { session_id: sid, controllers, markers } = st.bench
  const op = client(request, await login('operator@dronematrx.com'))
  const admin = client(request, await login('admin@dronematrx.com'))
  results.bench = { session_id: sid, controllers, startup_s: st.bench.startup_s, wall_s: st.benchWallS }
  results.started_at = st.startedAt
  const fleet0 = (await op.get('/mission-control/fleet')).body
  const sims = fleet0.drones.filter((d: { simulated: boolean }) => d.simulated).slice(0, 3) as { id: string; name: string }[]
  const [d1, d2, d3] = sims
  results.drones = sims.map((d) => d.name)
  const marker = markers[0]
  // Record test/01 from the start so the DVR has footage by the time it is tested.
  const feedsInit = (await op.get('/video/feeds')).body
  const test01 = feedsInit.feeds.find((f: { path: string }) => f.path === 'test/01/hi')
  expect((await op.post(`/video/feeds/${test01.id}/recordings`, { record: true })).body.recording).toBe(true)

  // ------------------------------------------------------------------ 1-15
  await test.step('Log in, open Mission Control, three panels', async () => {
    await uiLogin(page, 'operator@dronematrx.com')
    await page.goto('/mission-control')
    await expect(page.getByTestId('mc-left')).toBeVisible()
    await expect(page.getByTestId('mc-map')).toBeVisible()
    await expect(page.getByTestId('mc-right')).toBeVisible()
    await page.waitForFunction(() => (window as unknown as { __dmxDashboard?: { readyAt?: number } }).__dmxDashboard?.readyAt !== undefined)
    // Fresh load for the load-time metric (navigation start -> fleet rendered).
    await page.reload()
    await page.waitForFunction(() => (window as unknown as { __dmxDashboard?: { readyAt?: number } }).__dmxDashboard?.readyAt !== undefined)
    results.dashboard_load = await page.evaluate(() => {
      const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming
      return { ...(window as unknown as { __dmxDashboard: object }).__dmxDashboard, domContentLoadedMs: nav.domContentLoadedEventEnd, loadEventMs: nav.loadEventEnd }
    })
    await page.waitForTimeout(2500)
    await shot(page, '01_mission_control_dashboard.png')
    steps['dashboard'] = 'PASS'
  })

  await test.step('Fleet list and live telemetry', async () => {
    await expect(page.getByTestId('mc-fleet-list')).toBeVisible()
    await expect(page.getByTestId('mc-conn-DMX-001')).toHaveText('CONNECTED')
    const before = await page.getByTestId('mc-age-DMX-001').getAttribute('title')
    await page.waitForTimeout(3000)
    const after = await page.getByTestId('mc-age-DMX-001').getAttribute('title')
    expect(after).not.toBe(before) // the telemetry timestamp keeps moving
    await page.getByTestId('mc-filter-connected').click()
    await shot(page, '02_live_fleet.png')
    await page.getByTestId('mc-filter-all').click()
    steps['live_telemetry'] = 'PASS'
  })

  // ---------------------------------------------------------- 16-19 binding
  await test.step('Auto-discover and bind (discovery, QR camera scan, manual)', async () => {
    await selectRow(page, d1.name)
    await page.getByTestId('mc-open-binding').click()
    const t0 = Date.now()
    await page.getByTestId('mc-discover').click()
    await expect(page.getByTestId(`mc-fc-${controllers[0]}`)).toBeVisible({ timeout: 5000 })
    results.discovery_ui_ms = Date.now() - t0
    results.discovery_api = (await op.post('/mission-control/fc/discover')).body.discovery_ms
    await expect(page.getByTestId(`mc-fc-${controllers[0]}`)).toContainText('PX4 v1.15.4')
    await shot(page, '04_flight_controller_discovery.png')
    await page.getByTestId('mc-bind-drone').selectOption(d1.id)
    await page.getByTestId(`mc-bind-${controllers[0]}`).click()
    await expect(page.getByTestId('mc-bind-done')).toContainText(`bound to ${d1.name}`)
    await expect(page.getByTestId(`mc-fc-${controllers[0]}`)).toContainText(d1.name)
    await shot(page, '05_flight_controller_binding.png')
    // QR: the fake camera shows controller 02's binding QR code.
    await page.getByTestId('mc-bind-drone').selectOption(d2.id)
    await page.getByTestId('mc-bind-tab-qr').click()
    await page.getByTestId('mc-qr-camera').click()
    await expect(page.getByTestId('mc-qr-fc')).toHaveText(`sitl:${sid}:${controllers[1]}`, { timeout: 20_000 })
    await page.getByTestId('mc-qr-bind').click()
    await expect(page.getByTestId('mc-bind-done')).toContainText('bound via QR')
    // Manual entry (server-validated).
    await page.getByTestId('mc-bind-drone').selectOption(d3.id)
    await page.getByTestId('mc-bind-tab-manual').click()
    await page.getByTestId('mc-manual-endpoint').fill('sitl:bench-zzzzzz:../etc')
    await page.getByTestId('mc-manual-bind').click()
    await expect(page.getByTestId('mc-bind-error')).toContainText('SITL endpoint must be')
    await page.getByTestId('mc-manual-endpoint').fill(`sitl:${sid}:${controllers[2]}`)
    await page.getByTestId('mc-manual-bind').click()
    await expect(page.getByTestId('mc-bind-done')).toContainText('bound manually')
    await page.getByTestId('mc-bind-close').click()
    const binds = (await op.get('/mission-control/fc/bindings')).body as { drone_id: string; state: string; bound_via: string }[]
    results.bindings = binds.map((b) => ({ drone: sims.find((s) => s.id === b.drone_id)?.name, state: b.state, via: b.bound_via }))
    expect(binds.filter((b) => b.state === 'bound').map((b) => b.bound_via).sort()).toEqual(['discovery', 'manual', 'qr'])
    // Persists across a page refresh.
    await page.reload()
    await expect(page.getByTestId(`mc-conn-${d1.name}`)).toHaveText('CONNECTED', { timeout: 20_000 })
    await selectRow(page, d1.name)
    await expect(page.getByTestId('mc-binding')).toContainText(controllers[0])
    steps['fc_discovery'] = 'PASS'
    steps['fc_binding'] = 'PASS'
  })

  // --------------------------------------------------- 13-15 selection sync
  await test.step('Selection is shared by fleet list, map and video wall', async () => {
    await selectRow(page, d1.name)
    await expect(page.locator(`[data-testid="mc-marker-${d1.id}"]`)).toHaveAttribute('data-selected', 'true')
    await expect(page.locator(`[data-testid="mc-tile-sitl/${controllers[0]}/hi"]`)).toHaveAttribute('data-selected', 'true', { timeout: 20_000 })
    // Map -> fleet: clicking another drone's marker selects it in the list.
    await centre(page, marker.latitude - 0.0005, marker.longitude, 18)
    // Pads are 8 m apart: icons overlap at this zoom, so click SIM-02's own marker element.
    await page.locator(`[data-testid="mc-marker-${d2.id}"]`).dispatchEvent('click')
    await expect(droneRow(page, d2.name)).toHaveAttribute('aria-selected', 'true')
    await selectRow(page, d1.name)
    steps['leaflet_sync'] = 'PASS'
  })

  // ------------------------------------------------------------- 20-24 arm
  await test.step('Pre-arm: a failed required check blocks arming (NFZ on the pad)', async () => {
    const pad = (await op.get(`/mission-control/drones/${d3.id}`)).body
    const k = 0.00004 // ~4.4 m half-width: SIM-03's pad only (pads are 8 m apart)
    const nfz = await admin.post('/geofences', {
      name: 'E2E Sprint6 NFZ', type: 'nfz',
      geometry: { type: 'Polygon', coordinates: [[[pad.longitude - k, pad.latitude - k], [pad.longitude + k, pad.latitude - k], [pad.longitude + k, pad.latitude + k], [pad.longitude - k, pad.latitude + k], [pad.longitude - k, pad.latitude - k]]] },
    })
    expect(nfz.status).toBe(201)
    await selectRow(page, d3.name)
    await page.getByTestId('mc-prearm-refresh').click()
    await expect(page.getByTestId('prearm-operating_area')).toHaveAttribute('data-status', 'FAIL')
    await expect(page.getByTestId('mc-arm')).toBeDisabled()
    await shot(page, '06_prearm_checklist.png')
    const blocked = await op.post(`/mission-control/drones/${d3.id}/commands`, { command: 'ARM', confirm_mode: 'sitl' })
    expect(blocked.status).toBe(409)
    expect(blocked.body.code).toBe('PREARM_FAILED')
    results.prearm_blocked = { blocking: blocked.body.prearm.blocking }
    expect(await admin.del(`/geofences/${nfz.body.id}`)).toBeLessThan(300)
    steps['prearm'] = 'PASS'
  })

  await test.step('Arm through the checked flow; armed state confirmed by telemetry', async () => {
    await selectRow(page, d1.name)
    // PX4 SITL's EKF can briefly report "height estimate not stable" at reduced real-time
    // factor; the backend re-check then refuses ARM (correctly). Arm like an operator:
    // wait for a stable READY (3 consecutive passing checks), retry after a refusal.
    const armAttempts: { refused: string | null }[] = []
    for (let attempt = 0; attempt < 5; attempt++) {
      await expect.poll(async () => {
        let ok = 0
        for (let i = 0; i < 3; i++) {
          if ((await op.get(`/mission-control/drones/${d1.id}/prearm?expected_mode=sitl`)).body.can_arm) ok++
          await page.waitForTimeout(700)
        }
        return ok
      }, { timeout: 90_000, intervals: [1000] }).toBe(3)
      await page.getByTestId('mc-prearm-refresh').click()
      const ready = await page.getByTestId('mc-prearm-verdict').filter({ hasText: 'READY TO ARM' }).waitFor({ timeout: 15_000 }).then(() => true).catch(() => false)
      if (!ready) {
        const blocking = await page.locator('[data-testid^="prearm-"][data-status="FAIL"], [data-testid^="prearm-"][data-status="UNKNOWN"]').evaluateAll((els) => els.map((e) => e.getAttribute('data-testid')))
        armAttempts.push({ refused: `UI pre-arm blocked: ${blocking.join(', ')}` })
        continue
      }
      await page.getByTestId('mc-arm').click()
      await expect(page.getByTestId('mc-confirm-ok')).toBeDisabled() // operator must confirm SITL mode
      await expect(page.getByTestId('mc-confirm-takeoff')).toBeChecked()
      await page.getByTestId('mc-confirm-mode').check()
      await page.getByTestId('mc-confirm-ok').click()
      const armed = await page.getByTestId('mc-armed').filter({ hasText: /^ARMED$/ }).waitFor({ timeout: 8000 }).then(() => true).catch(() => false)
      const err = await page.getByTestId('mc-cmd-error').textContent().catch(() => null)
      armAttempts.push({ refused: armed ? null : err })
      if (armed) break
    }
    results.arm_attempts = armAttempts
    // "Take off once armed" (default on) sends TAKEOFF as soon as telemetry shows ARMED.
    await expect(page.getByTestId('cmd-state-ARM').first()).toHaveAttribute('data-state', 'confirmed', { timeout: 30_000 })
    await expect(page.getByTestId('cmd-state-TAKEOFF').first()).toHaveAttribute('data-state', 'confirmed', { timeout: 90_000 })
    await expect(page.getByTestId('mc-armed')).toHaveText('ARMED')
    // Second drone airborne too (API, same checks), for the fleet commands.
    const arm2: { status: number; code?: string; blocking?: string[] }[] = []
    let armed2 = false
    for (let attempt = 0; attempt < 5 && !armed2; attempt++) {
      await expect.poll(async () => (await op.get(`/mission-control/drones/${d2.id}/prearm?expected_mode=sitl`)).body.can_arm, { timeout: 90_000, intervals: [1500] }).toBe(true)
      const a2 = await op.post(`/mission-control/drones/${d2.id}/commands`, { command: 'ARM', confirm_mode: 'sitl', idempotency_key: `arm-${Date.now()}` })
      arm2.push({ status: a2.status, code: a2.body.code, blocking: a2.body.prearm?.blocking })
      if (a2.body.state !== 'accepted') continue
      await waitCommand(op, a2.body.id, ['confirmed'])
      const t2 = await op.post(`/mission-control/drones/${d2.id}/commands`, { command: 'TAKEOFF', params: { altitude_m: 15 }, confirm_mode: 'sitl' })
      await waitCommand(op, t2.body.id, ['confirmed'], 90_000)
      armed2 = true
    }
    results.arm_attempts_api = arm2
    expect(armed2).toBe(true)
    steps['arm'] = 'PASS'
  })

  // Gazebo bench recording (real simulator view) while the drones fly.
  const models = (await sim(request, 'GET', `/sessions/${sid}`)).instances.map((i: { gz_model_name: string }) => i.gz_model_name)
  await sim(request, 'POST', `/sessions/${sid}/gui/start`)
  await sim(request, 'POST', `/sessions/${sid}/gui/follow`, { model: models[0], offset: [-14, -10, 6] })
  await sim(request, 'POST', `/sessions/${sid}/gui/record/start`, { filename: 'sprint6_gazebo_bench.mp4' })

  // ------------------------------------------------------- 31-36 commands
  await test.step('GO TO a valid destination; a prohibited one is rejected', async () => {
    await centre(page, marker.latitude, marker.longitude, 18)
    await page.getByTestId('mc-goto-pick').click()
    await clickMap(page, marker.latitude, marker.longitude)
    await expect(page.getByTestId('mc-goto-panel')).toBeVisible()
    await page.getByTestId('mc-goto-send').click()
    await page.getByTestId('mc-confirm-mode').check()
    await page.getByTestId('mc-confirm-ok').click()
    await expect(page.getByTestId('cmd-state-GOTO').first()).toHaveAttribute('data-state', /accepted|confirmed/)
    const gotoRow = (await op.get(`/mission-control/commands?drone_id=${d1.id}`)).body.find((c: { command: string }) => c.command === 'GOTO')
    await waitCommand(op, gotoRow.id, ['confirmed'], 120_000)
    await page.waitForTimeout(1500)
    await shot(page, '03_leaflet_drone_tracking.png')
    // Prohibited: outside the bench operating area (and toward the seeded airport NFZ).
    const bad = await op.post(`/mission-control/drones/${d1.id}/commands`, { command: 'GOTO', params: { latitude: 23.2276, longitude: 72.6459, altitude_m: 30 }, confirm_mode: 'sitl' })
    expect(bad.status).toBe(409)
    expect(bad.body.code).toBe('ZONE_VIOLATION')
    results.goto_rejected = bad.body.detail
    steps['quick_commands_goto'] = 'PASS'
  })

  // ----------------------------------------------------------- 25-30 video
  await test.step('Video wall layouts, fullscreen, PiP, link loss + recovery, DVR', async () => {
    await page.getByTestId('mc-tab-video').click()
    await page.getByTestId('mc-focus-video').click()
    await page.getByTestId('mc-layout-1').click()
    await page.waitForTimeout(3000)
    await shot(page, '07_video_wall_single.png')
    await page.getByTestId('mc-layout-4').click()
    await expect(page.locator('.dm-video-tile[data-state="connected"], .dm-video-tile[data-state="degraded"]')).toHaveCount(4, { timeout: 30_000 })
    await shot(page, '08_video_wall_four.png')
    await page.getByTestId('mc-layout-9').click()
    await expect(page.locator('.dm-video-tile[data-state="connected"], .dm-video-tile[data-state="degraded"]')).toHaveCount(9, { timeout: 40_000 })
    const nine = await page.locator('.dm-video-tile[data-feed-id]').evaluateAll((els) => els.map((e) => e.getAttribute('data-testid')))
    expect(new Set(nine).size).toBe(9) // nine distinct sources, no duplicated stream
    results.nine_feeds = nine
    await shot(page, '09_video_wall_nine.png')
    // Fullscreen on a real tile.
    const tile = page.locator('[data-testid="mc-tile-test/01/hi"]')
    await tile.getByTestId('tile-fullscreen').click()
    await page.waitForFunction(() => !!document.fullscreenElement)
    await page.waitForTimeout(1500)
    await shot(page, '10_video_fullscreen.png')
    await page.evaluate(() => document.exitFullscreen())
    await page.waitForFunction(() => !document.fullscreenElement)
    // Picture-in-Picture: real API when the browser offers it.
    const pip = await page.evaluate(() => ({ enabled: 'pictureInPictureEnabled' in document && document.pictureInPictureEnabled }))
    results.pip = pip
    if (pip.enabled) {
      await tile.getByTestId('tile-pip').click()
      results.pip_entered = await page.waitForFunction(() => !!document.pictureInPictureElement, null, { timeout: 5000 }).then(() => true).catch(() => false)
      if (results.pip_entered) await page.evaluate(() => document.exitPictureInPicture())
    } else {
      await expect(tile.getByTestId('tile-pip')).toBeDisabled()
    }
    steps['pip'] = pip.enabled && results.pip_entered ? 'PASS' : 'BLOCKED'
    // Controlled link loss: stop the test-pattern publisher.
    await page.getByTestId('mc-layout-4').click()
    await expect(tile).toHaveAttribute('data-state', /connected|degraded/, { timeout: 30_000 })
    const t0 = Date.now()
    await request.post(`${SIMHOST}/video/test-sources/1/stop`)
    await expect(tile.getByTestId('tile-link-loss')).toBeVisible({ timeout: 10_000 })
    results.link_loss_overlay_ms = Date.now() - t0
    await shot(page, '11_video_link_loss.png')
    await request.post(`${SIMHOST}/video/test-sources/1/start`)
    await expect(tile).toHaveAttribute('data-state', /connected|degraded/, { timeout: 40_000 })
    results.reconnect_ms = Date.now() - t0
    steps['webrtc_reconnection'] = 'PASS'
    // Glass-to-glass latency samples (barcode on test patterns) and RTF-independent stats.
    results.video_latency = await page.evaluate(() => (window as unknown as { __dmxVideoLatency?: { latencyMs: number; feed: string }[] }).__dmxVideoLatency ?? [])
    results.video_state = await page.evaluate(() => Object.fromEntries(Object.entries((window as unknown as { __dmxVideoState?: Record<string, { state: string; rendition: string; stats: unknown }> }).__dmxVideoState ?? {}).map(([k, v]) => [k, { state: v.state, rendition: v.rendition, stats: v.stats }])))
    // DVR: recorded since the start of the test.
    await tile.getByTestId('tile-dvr').click()
    await expect(page.getByTestId('dvr-range')).toBeVisible({ timeout: 30_000 })
    await page.getByTestId('dvr-load').click()
    await expect(page.getByTestId('dvr-video')).toBeVisible({ timeout: 30_000 })
    const dvr = page.getByTestId('dvr-video')
    await dvr.evaluate((v: HTMLVideoElement) => new Promise((r) => (v.readyState >= 2 ? r(null) : v.addEventListener('loadeddata', () => r(null), { once: true }))))
    const readBarcode = () => dvr.evaluate((v: HTMLVideoElement) => {
      const c = document.createElement('canvas')
      c.width = v.videoWidth; c.height = 40
      const ctx = c.getContext('2d')!
      ctx.drawImage(v, 0, 0, v.videoWidth, 40, 0, 0, v.videoWidth, 40)
      const px = ctx.getImageData(0, 0, c.width, 40).data
      const s = c.width / 480
      let ms = 0
      for (let i = 0; i < 48; i++) {
        const o = (Math.floor(16 * s) * c.width + Math.floor((8 + i * 6 + 3) * s)) * 4
        if ((px[o] + px[o + 1] + px[o + 2]) / 3 > 128) ms += 2 ** i
      }
      return { t: v.currentTime, captureMs: ms }
    })
    const a = await readBarcode()
    await page.getByTestId('dvr-scrub').fill('20')
    await dvr.evaluate((v: HTMLVideoElement) => new Promise((r) => (v.seeking ? v.addEventListener('seeked', () => r(null), { once: true }) : r(null))))
    await page.waitForTimeout(600)
    const b = await readBarcode()
    results.dvr_seek = { before: a, after: b, positionDeltaS: b.t - a.t, recordedTimeDeltaS: (b.captureMs - a.captureMs) / 1000 }
    expect(b.t - a.t).toBeGreaterThan(15)
    expect(Math.abs((b.captureMs - a.captureMs) / 1000 - (b.t - a.t))).toBeLessThan(2) // the frame shown is from the seeked time
    await page.getByTestId('dvr-play').click()
    await page.waitForTimeout(1200)
    await shot(page, '12_video_dvr.png')
    steps['video_layouts'] = 'PASS'
    steps['dvr'] = 'PASS'
    await page.getByTestId('mc-focus-video').click()
  })

  await test.step('HOLD, RTL and Assign Task', async () => {
    await selectRow(page, d1.name)
    await page.getByTestId('mc-hold').click()
    await expect(page.getByTestId('cmd-state-HOLD').first()).toHaveAttribute('data-state', 'confirmed', { timeout: 30_000 })
    await selectRow(page, d2.name)
    await page.getByTestId('mc-rtl').click()
    await expect(page.getByTestId('cmd-state-RTL').first()).toHaveAttribute('data-state', 'confirmed', { timeout: 30_000 })
    // Assign Task on a drone with a live CBBA mission (the seeded kinematic fleet).
    const fleet = (await op.get('/mission-control/fleet')).body.drones as { id: string; name: string; mission: { mission_id: string } | null }[]
    const withMission = fleet.find((d) => d.mission)
    if (withMission) {
      const tasks = (await op.get(`/missions/${withMission.mission!.mission_id}/cbba`)).body.tasks as { task_key: string; status: string; assigned_drone_id: string | null }[]
      // A task another drone holds, so the assignment is a real change.
      const task = tasks.find((t) => t.status !== 'completed' && t.assigned_drone_id && t.assigned_drone_id !== withMission.id)!
      await selectRow(page, withMission.name)
      await page.getByTestId('mc-task-key').fill(task.task_key)
      await page.getByTestId('mc-assign-task').click()
      await expect(page.getByTestId('mc-assign-result')).toContainText(`${task.task_key} assigned to ${withMission.name}`)
      await expect(page.getByTestId('mc-cmd-error')).toHaveCount(0)
      // Persisted: repeating the same assignment is a no-op.
      const again = await op.post(`/mission-control/drones/${withMission.id}/assign-task`, { mission_id: withMission.mission!.mission_id, task_key: task.task_key })
      expect(again.status).toBe(200)
      expect(again.body.changed).toBe(false)
      results.assign_task = { drone: withMission.name, task: task.task_key, persisted: again.body.changed === false }
      steps['assign_task'] = 'PASS'
    } else steps['assign_task'] = 'BLOCKED (no drone on a live mission)'
    steps['quick_commands'] = 'PASS'
  })

  // ------------------------------------------------------ 37-40 fleet-wide
  await test.step('HOLD ALL: confirmation, cancel dispatches nothing, confirm gives per-drone results', async () => {
    // d2 is returning (RTL); send it back up so two SITL drones fly.
    await selectRow(page, d1.name)
    const before = ((await op.get('/mission-control/commands?limit=500')).body as unknown[]).length
    await page.getByTestId('mc-hold-all').click()
    await expect(page.getByTestId('mc-fleet-targets')).toBeVisible()
    await shot(page, '13_quick_action_confirmation.png')
    await page.getByTestId('mc-fleet-cancel').click()
    await page.waitForTimeout(1500)
    expect(((await op.get('/mission-control/commands?limit=500')).body as unknown[]).length).toBe(before) // nothing dispatched
    await page.getByTestId('mc-hold-all').click()
    await expect(page.getByTestId('mc-fleet-targets')).toBeVisible()
    const targets = await page.getByTestId('mc-fleet-targets').locator('tr').count()
    await page.getByTestId('mc-fleet-confirm').click()
    await expect(page.getByTestId('mc-fleet-results')).toBeVisible()
    await expect(page.getByTestId('mc-fleet-results').locator('[data-state="confirmed"]')).toHaveCount(targets, { timeout: 60_000 })
    await shot(page, '14_command_result.png')
    results.hold_all = { targets, results: await page.getByTestId('mc-fleet-results').locator('tr').allInnerTexts() }
    await page.getByTestId('mc-fleet-close').click()
    steps['fleet_commands'] = 'PASS'
  })

  // ------------------------------------------------------- 41-50 detections
  await test.step('Detections: fixtures + real SITL detector, accept/reject, PostGIS, heatmap, audit', async () => {
    await page.getByTestId('mc-tab-detections').click()
    const emitted = await admin.post('/detections/test-fixtures', { drone_id: d1.id, count: 2, location: 'valid', anchor_lat: marker.latitude, anchor_lon: marker.longitude })
    const missing = await admin.post('/detections/test-fixtures', { drone_id: d1.id, count: 1, location: 'missing' })
    const invalid = await admin.post('/detections/test-fixtures', { drone_id: d1.id, count: 1, location: 'invalid' })
    results.fixtures_via = emitted.body.via
    const first = emitted.body.emitted[0].event_id as string
    const second = emitted.body.emitted[1].event_id as string
    const card1 = page.getByTestId(`det-card-${first}`)
    await expect(card1).toBeVisible()
    await expect(card1.getByTestId('det-thumb')).toBeVisible()
    await expect(card1.getByTestId('det-confidence')).toHaveText(/%$/)
    await expect(card1.getByTestId('det-location')).toBeVisible()
    // Scoped to this run's cards (earlier runs may have left pending fixtures).
    await expect(page.getByTestId(`det-card-${missing.body.emitted[0].event_id}`).getByTestId('det-no-location')).toBeVisible()
    await expect(page.getByTestId(`det-card-${invalid.body.emitted[0].event_id}`).getByTestId('det-no-location')).toContainText('invalid coordinates')
    // The real detector: SIM-01 hovered over the red marker with its camera.
    const simCard = page.locator('.dm-det-card', { hasText: 'SITL CV DETECTOR' }).first()
    results.sitl_detector_card = await simCard.isVisible({ timeout: 30_000 }).catch(() => false)
    await shot(page, '15_detection_feed.png')
    results.detection_latency = await page.evaluate(() => (window as unknown as { __dmxDetectionLatency?: unknown[] }).__dmxDetectionLatency ?? [])
    // The feed defaults to PENDING (reviewed cards leave it): review in the ALL view.
    await page.getByTestId('det-filter-all').click()
    await card1.getByTestId('det-accept').click()
    await expect(card1.getByTestId('det-status')).toHaveText('ACCEPTED')
    const persisted = ((await op.get('/detections?status=accepted')).body as { event_id: string; review_status: string; reviewed_by: string | null }[]).find((d) => d.event_id === first)
    expect(persisted?.reviewed_by).toBeTruthy()
    await shot(page, '16_detection_accepted.png')
    const card2 = page.getByTestId(`det-card-${second}`)
    await card2.getByTestId('det-reject').click()
    await card2.getByTestId('det-reject-reason').fill('E2E: duplicate of accepted target')
    await card2.getByTestId('det-reject-confirm').click()
    await expect(card2.getByTestId('det-status')).toHaveText('REJECTED')
    const gj = (await op.get('/detections/accepted.geojson')).body as { features: { properties: { id: string } }[] }
    const ids = (await op.get('/detections')).body as { id: string; event_id: string }[]
    const id1 = ids.find((d) => d.event_id === first)!.id
    const id2 = ids.find((d) => d.event_id === second)!.id
    expect(gj.features.some((f) => f.properties.id === id1)).toBe(true)
    expect(gj.features.some((f) => f.properties.id === id2)).toBe(false) // rejected: not in the accepted layer
    await centre(page, marker.latitude, marker.longitude, 19)
    await expect(page.locator(`.dm-det-marker-${id1}`)).toHaveCount(1)
    await shot(page, '17_detection_map_overlay.png')
    await page.getByTestId('mc-layers-toggle').click()
    await page.getByTestId('mc-layer-heatmap').check()
    await expect(page.locator('.dm-heat-cell').first()).toBeVisible({ timeout: 15_000 })
    const heat = (await op.get('/detections/heatmap?cell_m=20')).body
    results.heatmap = { cells: heat.cells.length, total: heat.total, max: heat.max }
    await shot(page, '18_detection_heatmap.png')
    await page.getByTestId('mc-layer-heatmap').uncheck()
    await page.getByTestId('mc-layers-toggle').click()
    const audit = (await op.get('/mission-control/audit?limit=300')).body as { action: string; user: string | null }[]
    const actions = new Set(audit.map((a) => a.action))
    for (const a of ['fc_bind', 'command_requested', 'command_accepted', 'command_confirmed', 'command_refused', 'detection_accepted', 'detection_rejected', 'video_recording_started'])
      expect(actions.has(a), a).toBe(true)
    results.audit_actions = [...actions].sort()
    steps['detection_review'] = 'PASS'
    steps['postgis'] = 'PASS'
  })

  // --------------------------------------------------------- 51-53 RBAC
  await test.step('Read-only user: controls hidden, backend refuses', async () => {
    const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
    const ro = await ctx.newPage()
    await uiLogin(ro, 'observer@dronematrx.com')
    await ro.goto('/mission-control')
    await expect(ro.getByTestId('mc-read-only')).toBeVisible()
    await expect(ro.getByTestId('mc-hold-all')).toHaveCount(0)
    await expect(ro.getByTestId('mc-open-binding')).toHaveCount(0)
    await ro.getByTestId(`mc-drone-${d1.name}`).click()
    await expect(ro.getByTestId('mc-quick-actions')).toHaveCount(0)
    await ro.getByTestId('mc-tab-detections').click()
    await expect(ro.getByTestId('det-accept')).toHaveCount(0)
    await ro.waitForTimeout(1500)
    await ro.screenshot({ path: path.join(EVIDENCE, '19_rbac_read_only.png') })
    const obs = client(request, await login('observer@dronematrx.com'))
    const denied = {
      command: (await obs.post(`/mission-control/drones/${d1.id}/commands`, { command: 'HOLD', confirm_mode: 'sitl' })).status,
      fleet: (await obs.post('/mission-control/fleet-commands', { fleet_command: 'HOLD_ALL', expected_targets: [], confirm: true, idempotency_key: 'observer-try-1' })).status,
      discover: (await obs.post('/mission-control/fc/discover')).status,
      review: (await obs.post(`/detections/${(await op.get('/detections')).body[0].id}/review`, { decision: 'accepted' })).status,
      recording: (await obs.post(`/video/feeds/${test01.id}/recordings`, { record: false })).status,
    }
    results.rbac_denied = denied
    expect(Object.values(denied).every((s) => s === 403)).toBe(true)
    await ctx.close()
    steps['rbac'] = 'PASS'
  })

  // ---------------------------------------------------- 54-57 stale/restore
  await test.step('Telemetry loss shows STALE, recovery reconnects', async () => {
    await page.getByTestId('mc-filter-sitl').click()
    await sim(request, 'POST', `/sessions/${sid}/failures`, { type: 'link', uin: controllers[2], params: { down: true } })
    const t0 = Date.now()
    await expect(page.getByTestId(`mc-conn-${d3.name}`)).toHaveText('STALE', { timeout: 20_000 })
    results.stale_after_ms = Date.now() - t0
    await expect(page.getByTestId('mc-stale-banner')).toBeVisible()
    await shot(page, '20_stale_telemetry.png')
    await sim(request, 'POST', `/sessions/${sid}/failures`, { type: 'link', uin: controllers[2], params: { down: false } })
    const t1 = Date.now()
    await expect(page.getByTestId(`mc-conn-${d3.name}`)).toHaveText('CONNECTED', { timeout: 30_000 })
    results.recovered_after_ms = Date.now() - t1
    await page.getByTestId('mc-filter-all').click()
    await page.waitForTimeout(1500)
    await shot(page, '21_mission_control_recovered.png')
    steps['stale_recovery'] = 'PASS'
  })

  // ------------------------------------------------- performance (in-browser)
  await test.step('Frame rate and telemetry cadence', async () => {
    results.fps = await page.evaluate(() => new Promise((resolve) => {
      const times: number[] = []
      const t0 = performance.now()
      const tick = (t: number) => { times.push(t); if (t - t0 < 5000) requestAnimationFrame(tick); else resolve({ frames: times.length, seconds: (t - t0) / 1000, fps: times.length / ((t - t0) / 1000), worstFrameMs: Math.max(...times.slice(1).map((x, i) => x - times[i])) }) }
      requestAnimationFrame(tick)
    }))
    const arrivals = await page.evaluate(() => (window as unknown as { __dmxTelemetryArrivals: Record<string, number[]> }).__dmxTelemetryArrivals)
    const intervals = (ids: string[]) => ids.flatMap((id) => (arrivals[id] ?? []).slice(1).map((t, i) => t - arrivals[id][i]))
    results.telemetry_intervals = { sitl: stats(intervals(sims.map((s) => s.id))) }
  })

  // ------------------------------------------------------- 58-59 cleanup
  await test.step('LAND ALL (typed confirmation), stop services, nothing left running', async () => {
    await sim(request, 'POST', `/sessions/${sid}/gui/record/stop`).catch(() => undefined)
    await page.getByTestId('mc-land-all').click()
    await expect(page.getByTestId('mc-fleet-confirm')).toBeDisabled()
    await page.getByTestId('mc-fleet-text').fill('LAND ALL')
    await page.getByTestId('mc-fleet-confirm').click()
    await expect(page.getByTestId('mc-fleet-results')).toBeVisible()
    await page.getByTestId('mc-fleet-close').click()
    await expect.poll(async () => ((await op.get('/mission-control/fleet')).body.drones as { id: string; armed: boolean }[]).filter((d) => sims.some((s) => s.id === d.id) && d.armed).length,
      { timeout: 120_000, intervals: [3000] }).toBe(0)
    const inflight = ((await op.get('/mission-control/commands?limit=500')).body as { state: string }[]).filter((c) => ['requested', 'dispatched', 'accepted'].includes(c.state))
    results.inflight_commands_after = inflight.length
    expect(inflight.length).toBe(0)
    // Copy the Gazebo recording before the bench (and its workspace) is stopped.
    const gz = path.resolve(process.cwd(), `../backend/sim_workspace/${sid}/evidence/sprint6_gazebo_bench.mp4`)
    fs.mkdirSync(path.join(DOCS, 'recordings'), { recursive: true })
    if (fs.existsSync(gz)) fs.copyFileSync(gz, path.join(DOCS, 'recordings', 'sprint6_gazebo_bench.mp4'))
    await op.post(`/video/feeds/${test01.id}/recordings`, { record: false })
    await request.delete(`${SIMHOST}/video/test-sources`)
    expect(await op.del(`/mission-control/bench/${sid}`)).toBe(200)
    const procs = await sim(request, 'GET', '/processes')
    results.px4_processes_after = procs.px4.length
    expect(procs.px4.length).toBe(0)
    steps['cleanup'] = 'PASS'
  })

  // Summary page (rendered from the measured results).
  results.finished_at = new Date().toISOString()
  results.duration_s = (Date.now() - Date.parse(st.startedAt)) / 1000
  const rows = Object.entries(steps).map(([k, v]) => `<tr><td>${k}</td><td style="color:${v === 'PASS' ? '#5CC28A' : '#FFD08A'}">${v}</td></tr>`).join('')
  await page.setContent(`<html><body style="background:#1d2230;color:#e8eaf0;font-family:monospace;padding:24px">
    <h2>Drone MatrX Sprint 6 E2E: ${testInfo.title}</h2>
    <p>Bench ${sid} (PX4 v1.15.4 SITL x3, Gazebo) · ${new Date().toISOString()} · run ${Math.round(Number(results.duration_s))} s</p>
    <table style="border-collapse:collapse" border="1" cellpadding="6"><tr><th>Area</th><th>Result</th></tr>${rows}</table>
    <p>discovery ${results.discovery_ui_ms} ms (UI) · link-loss overlay ${results.link_loss_overlay_ms} ms · dashboard ready ${(results.dashboard_load as { navToReadyMs: number }).navToReadyMs.toFixed(0)} ms</p>
    </body></html>`)
  await shot(page, '22_e2e_test_summary.png')
  fs.mkdirSync(path.join(DOCS, 'recordings'), { recursive: true })
})

test.afterEach(async ({ page }, testInfo) => {
  const video = page.video()
  await page.close()
  fs.mkdirSync(path.join(DOCS, 'recordings'), { recursive: true })
  if (video) {
    const p = await video.path().catch(() => null)
    if (p && fs.existsSync(p)) fs.copyFileSync(p, path.join(DOCS, 'recordings', `sprint6_e2e_mission_control${testInfo.status === 'passed' ? '' : '_' + testInfo.status}.webm`))
  }
})
