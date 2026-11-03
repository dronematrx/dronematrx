/**
 * Auto Mission walkthrough (screenshots for the guide).
 *
 * Real-aircraft mode runs the REAL backend, bridge and UI against three
 * PRACTICE vehicles (hwbridge/tools/practice_vehicle.py --count 3: a MAVLink
 * responder, not ArduPilot). Simulation mode runs the real Gazebo/PX4 simhost.
 * Prerequisites: backend (HWBRIDGE_TOKEN matching the bridge), vite on 5173,
 * practice vehicles, a bridge connected to tcp:127.0.0.1:5760/5770/5780, simhost.
 *
 *   npx playwright test -c playwright.hw.config.ts auto-mission
 */
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import path from 'node:path'
import { API, login, PASSWORD } from './helpers'

const SHOTS = path.resolve(process.cwd(), '../documents/sprint6-2/screenshots')
const PILOT = 'http://127.0.0.1:5799'
const HOME: [number, number] = [23.24, 72.65]
// The practice bridge's token (its config file); the backend must use the same one.
const BRIDGE_TOKEN = process.env.DMX_BRIDGE_TOKEN ?? 'auto-e2e-token-0123456789abcdef'

async function shot(page: Page, name: string) {
  await page.getByTestId('auto-result').scrollIntoViewIfNeeded().catch(() => undefined)
  await page.waitForTimeout(700)
  await page.screenshot({ path: path.join(SHOTS, name) })
}

async function api(request: APIRequestContext, token: string, method: string, p: string, body?: unknown) {
  const r = await request.fetch(`${API}${p}`, { method, headers: { Authorization: `Bearer ${token}` }, data: body })
  return { status: r.status(), body: await r.json().catch(() => null) }
}

async function clickMap(page: Page, lat: number, lng: number) {
  // A real mouse click at the screen position of (lat, lng), like a user.
  const box = (await page.locator('[data-testid="auto-map"] .leaflet-container').boundingBox())!
  const pt = await page.evaluate(([la, ln]) => {
    const map = (window as unknown as { __dmMap: { latLngToContainerPoint: (p: [number, number]) => { x: number; y: number } } }).__dmMap
    return map.latLngToContainerPoint([la, ln])
  }, [lat, lng] as [number, number])
  await page.mouse.click(box.x + pt.x, box.y + pt.y)
  await page.waitForTimeout(300)
}

async function drawArea(page: Page, half: number) {
  await page.evaluate(([la, ln]) => {
    (window as unknown as { __dmMap: { setView: (p: [number, number], z: number) => void } }).__dmMap.setView([la, ln], 17)
  }, HOME)
  await page.waitForTimeout(800)
  await clickMap(page, HOME[0] - half, HOME[1] - half)
  await clickMap(page, HOME[0] + half, HOME[1] + half)
  await expect(page.getByTestId('auto-aoi-ok')).toBeVisible()
}

async function token(request: APIRequestContext) {
  return (await (await request.post(`${API}/auth/login`, { data: { email: 'operator@dronematrx.com', password: PASSWORD } })).json()).access_token as string
}

test('auto mission with real aircraft: one approval, staggered launch, dropout replacement', async ({ page, request }) => {
  test.setTimeout(10 * 60_000)
  const tk = await token(request)
  // Clean slate: the pilots set STABILIZE after landing and the operator releases
  // the override locks (as in Mission Control); then unlink controllers from earlier runs.
  for (const s of [1, 2, 3]) await request.post(`${PILOT}/pilot?sysid=${s}&mode=STABILIZE`)
  for (const fc of ['tcp5760-1', 'tcp5770-2', 'tcp5780-3'])
    await request.post(`http://127.0.0.1:8110/fc/${fc}/override/release`, { headers: { 'X-Bridge-Token': BRIDGE_TOKEN }, data: { by: 'walkthrough setup' } })
  for (const b of (await api(request, tk, 'GET', '/mission-control/fc/bindings')).body as { id: string; endpoint: string; state: string }[])
    if (b.endpoint.startsWith('tcp:127.0.0.1:57') && b.state !== 'unbound') await api(request, tk, 'POST', `/mission-control/fc/bindings/${b.id}/unbind`)
  // Three real-aircraft records with a working camera, linked to the practice vehicles (Drone Setup step 12 does this).
  const suffix = String(Date.now() % 100000)
  for (const n of [1, 2, 3]) {
    const d = (await api(request, tk, 'POST', '/drones', { name: `AUTO-0${n}-${suffix}`, uin: `DMX-AUTO-${n}-${suffix}`, model: 'Quad (ArduCopter)', firmware: 'ArduPilot', hardware: true })).body as { id: string }
    await api(request, tk, 'POST', `/drones/${d.id}/payloads`, { type: 'camera', name: 'Drone camera', status: 'calibrated' })
    const b = await api(request, tk, 'POST', '/mission-control/fc/bind', { drone_id: d.id, connection_type: 'udp', endpoint: `tcp:127.0.0.1:${5750 + 10 * n}/${n}`, via: 'manual' })
    expect(b.body.state).toBe('bound')
  }
  const names = [1, 2, 3].map((n) => `AUTO-0${n}-${suffix}`)
  await expect.poll(async () => ((await api(request, tk, 'GET', '/auto-missions/availability')).body.hardware.survey.ready as string[])
    .filter((x) => names.includes(x)).length, { timeout: 60_000 }).toBe(3)

  await login(page)
  await page.goto('/auto-mission')
  await page.getByTestId('auto-template-survey').click()
  await page.getByTestId('auto-mode-hardware').click()
  await page.getByTestId('auto-count').fill('2')
  await drawArea(page, 0.0007)
  await shot(page, 'A01_choose_type_area_count.png')

  await page.getByTestId('auto-plan').click()
  await expect(page.getByTestId('auto-result')).toBeVisible({ timeout: 90_000 })
  await expect(page.getByTestId('auto-approval')).toBeVisible()
  await shot(page, 'A02_planned_two_real_drones.png')

  for (const k of ['rc_pilot_ready', 'area_clear', 'observer_present', 'legal_ok', 'weather_ok']) await page.getByTestId(`auto-ack-${k}`).check()
  await page.getByTestId('auto-launch-hw').click()
  await expect(page.getByTestId('auto-launch-status')).toBeVisible({ timeout: 90_000 })
  const flying = page.locator('[data-testid^="auto-state-"]', { hasText: 'Flying' })
  await expect(flying).toHaveCount(2, { timeout: 60_000 }) // second drone starts 10 s after the first
  await shot(page, 'A03_launched_staggered.png')

  // The safety pilot of the first chosen drone takes over with the RC mode switch.
  const first = (await page.locator('[data-testid^="auto-drone-"]').first().getAttribute('data-testid'))!.replace('auto-drone-', '')
  const sysid = Number(first.split('-')[1].slice(1))
  await request.post(`${PILOT}/pilot?sysid=${sysid}&mode=LOITER`)
  await expect(page.getByTestId(`auto-state-${first}`)).toHaveText('Dropped out', { timeout: 30_000 })
  await shot(page, 'A04_pilot_takeover_dropped.png')

  await page.getByTestId(`auto-reassign-${first}`).click()
  await expect(page.getByTestId(`auto-state-${first}`)).toHaveText('Replaced', { timeout: 30_000 })
  await expect(page.locator('[data-testid^="auto-state-"]', { hasText: 'Flying' })).toHaveCount(2, { timeout: 60_000 }) // spare took over
  await shot(page, 'A05_spare_drone_took_over.png')

  // Cleanup: everyone lands.
  for (const s of [1, 2, 3]) await request.post(`${PILOT}/pilot?sysid=${s}&mode=LAND`)
})

test('auto mission in simulation: plan and launch Gazebo with one click', async ({ page }) => {
  test.setTimeout(10 * 60_000)
  await login(page)
  await page.goto('/auto-mission')
  await page.getByTestId('auto-template-survey').click()
  await page.getByTestId('auto-mode-simulation').click()
  await page.getByTestId('auto-count').fill('2')
  await drawArea(page, 0.0015)
  await page.getByTestId('auto-plan').click()
  await expect(page.getByTestId('auto-launch-sim')).toBeVisible({ timeout: 90_000 })
  await shot(page, 'A06_simulation_planned.png')
  await page.getByTestId('auto-launch-sim').click()
  await expect(page).toHaveURL(/\/missions\/[0-9a-f-]+\/simulate/, { timeout: 180_000 })
  await page.waitForTimeout(20_000)
  await shot(page, 'A07_simulation_running.png')
})
