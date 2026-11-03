/**
 * Hardware workflow walkthrough (screenshots for the setup guide).
 *
 * Runs the REAL bridge, backend and UI against PRACTICE vehicles
 * (hwbridge/tools/practice_vehicle.py: a MAVLink responder, not ArduPilot).
 * Prerequisites: backend (HWBRIDGE_TOKEN=practice-token), vite on 5173,
 * practice vehicles, `python -m hwbridge.server --config hwbridge/config.practice.json`.
 *
 *   npx playwright test -c playwright.hw.config.ts
 */
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import path from 'node:path'
import { API, login, PASSWORD } from './helpers'

const SHOTS = path.resolve(process.cwd(), '../documents/sprint6-1/screenshots')
const PILOT = 'http://127.0.0.1:5799'
const HOME: [number, number] = [23.24, 72.65]

async function shot(page: Page, name: string, focus?: string) {
  if (focus) await page.getByTestId(focus).first().scrollIntoViewIfNeeded().catch(() => undefined)
  await page.waitForTimeout(700)
  await page.screenshot({ path: path.join(SHOTS, name) })
}

async function api(request: APIRequestContext, token: string, method: string, p: string, body?: unknown) {
  const r = await request.fetch(`${API}${p}`, { method, headers: { Authorization: `Bearer ${token}` }, data: body })
  return { status: r.status(), body: r.status() === 204 ? null : await r.json().catch(() => null) }
}

async function clickMap(page: Page, lat: number, lng: number) {
  // Fire the map's own click event at exact coordinates (independent of scrolling).
  await centre(page, lat, lng, 18)
  await page.evaluate(([la, ln]) => {
    (window as unknown as { __dmMap: { fire: (t: string, e: unknown) => void } }).__dmMap.fire('click', { latlng: { lat: la, lng: ln } })
  }, [lat, lng] as [number, number])
}

async function centre(page: Page, lat: number, lng: number, zoom: number) {
  await page.evaluate(([la, ln, z]) => { (window as unknown as { __dmMap: { setView: (p: [number, number], z: number) => void } }).__dmMap.setView([la, ln], z) }, [lat, lng, zoom] as [number, number, number])
}

test('hardware workflow walkthrough', async ({ page, request }) => {
  test.setTimeout(15 * 60_000)
  const token = (await (await request.post(`${API}/auth/login`, { data: { email: 'operator@dronematrx.com', password: PASSWORD } })).json()).access_token as string

  // Clean slate from earlier walkthroughs: land the practice vehicles (an armed
  // drone cannot be unbound), clear the bridge's override flag, unbind, retire the records.
  const bridge = { 'X-Bridge-Token': 'practice-token' }
  for (const s of [1, 2]) await request.post(`${PILOT}/pilot?sysid=${s}&mode=LAND`)
  for (let i = 0; i < 90; i++) {
    const fcs = (await (await request.get('http://127.0.0.1:8110/fc', { headers: bridge })).json()).controllers as { armed: boolean }[]
    if (fcs.every((c) => !c.armed)) break
    await page.waitForTimeout(1000)
  }
  // After landing the pilot puts the mode switch back to STABILIZE (LAND is not armable).
  for (const s of [1, 2]) await request.post(`${PILOT}/pilot?sysid=${s}&mode=STABILIZE`)
  await page.waitForTimeout(2000)
  for (const fc of ['tcp5760-1', 'tcp5770-2']) await request.post(`http://127.0.0.1:8110/fc/${fc}/override/release`, { headers: bridge, data: { by: 'walkthrough setup' } })
  for (const b of (await api(request, token, 'GET', '/mission-control/fc/bindings')).body as { id: string; endpoint: string; state: string }[])
    if (b.endpoint.startsWith('tcp:127.0.0.1:57') && b.state !== 'unbound') await api(request, token, 'POST', `/mission-control/fc/bindings/${b.id}/unbind`)
  const existing = ((await api(request, token, 'GET', '/drones?limit=200')).body?.items ?? []) as { id: string; name: string; hardware?: boolean }[]
  for (const d of existing.filter((x) => x.name.startsWith('HW-0'))) await api(request, token, 'PATCH', `/drones/${d.id}`, { active: false, uin: null, name: `${d.name}-old-${Date.now() % 100000}` })

  // ---------------------------------------------------------- 1 register
  await login(page)
  await page.getByRole('button', { name: /New Drone/ }).click()
  const form = page.locator('div', { has: page.getByRole('heading', { name: 'Register Drone' }) }).last()
  await form.getByPlaceholder('DMX-009').fill('HW-01')
  await form.getByPlaceholder('M30').fill('F450 ArduCopter')
  await form.getByPlaceholder('DRN-01-999999').fill(`DRN-HW-01-${Date.now() % 100000}`)
  await page.getByTestId('drone-hardware').check()
  await shot(page, '01_register_real_aircraft.png')
  await page.getByRole('button', { name: 'Create' }).click()
  await expect(page.getByText('HW-01').first()).toBeVisible()
  const hw2 = await api(request, token, 'POST', '/drones', { name: 'HW-02', model: 'F450 ArduCopter', firmware: 'ArduPilot', hardware: true,
    uin: `DRN-HW-02-${Date.now() % 100000}`, latitude: HOME[0], longitude: HOME[1] })
  expect(hw2.status).toBe(201)
  const drones = ((await api(request, token, 'GET', '/drones?limit=200')).body.items as { id: string; name: string }[])
  const d1 = drones.find((d) => d.name === 'HW-01')!, d2 = drones.find((d) => d.name === 'HW-02')!
  for (const d of [d1, d2]) await api(request, token, 'POST', `/drones/${d.id}/payloads`, { type: 'camera', name: 'RTSP camera', status: 'calibrated' })

  // ---------------------------------------------------------- 2 bridge + discovery + binding
  await page.goto('/mission-control')
  await page.getByTestId('mc-open-binding').click()
  await page.getByTestId('mc-discover').click()
  await expect(page.getByTestId('mc-fc-tcp5760-1')).toBeVisible()
  await shot(page, '02_discover_ardupilot_controllers.png')
  await page.getByTestId('mc-bind-drone').selectOption(d1.id)
  await page.getByTestId('mc-bind-tcp5760-1').click()
  await expect(page.getByTestId('mc-fc-tcp5760-1')).toContainText('HW-01')
  await page.getByTestId('mc-bind-drone').selectOption(d2.id)
  await page.getByTestId('mc-bind-tcp5770-2').click()
  await expect(page.getByTestId('mc-fc-tcp5770-2')).toContainText('HW-02')
  await shot(page, '03_bound_to_hardware_drones.png')
  await page.getByTestId('mc-bind-close').click()

  // ---------------------------------------------------------- 3 pre-arm + safety panel
  await page.getByTestId('mc-drone-HW-01').click()
  await centre(page, HOME[0], HOME[1], 18)
  await expect(page.getByTestId('mc-prearm-verdict')).toHaveText('READY TO ARM', { timeout: 30_000 })
  await shot(page, '04_prearm_and_safety_panel.png', 'mc-prearm')
  await request.post(`${PILOT}/rc?sysid=1&on=0`)
  await page.waitForTimeout(1500)
  await page.getByTestId('mc-prearm-refresh').click()
  await expect(page.getByTestId('prearm-rc_link')).toHaveAttribute('data-status', 'FAIL')
  await shot(page, '05_rc_transmitter_off_blocks_arming.png', 'prearm-rc_link')
  await request.post(`${PILOT}/rc?sysid=1&on=1`)
  await page.waitForTimeout(1500)
  await page.getByTestId('mc-prearm-refresh').click()
  await expect(page.getByTestId('prearm-rc_link')).toHaveAttribute('data-status', 'PASS')

  // ---------------------------------------------------------- 4 permission
  const noPerm = await api(request, token, 'POST', `/mission-control/drones/${d1.id}/commands`, { command: 'ARM', params: {}, confirm_mode: 'hardware' })
  expect(noPerm.body.code).toBe('FLIGHT_NOT_AUTHORIZED')
  await page.getByTestId('hw-grant-open').click()
  for (const k of ['rc_pilot_ready', 'area_clear', 'observer_present', 'legal_ok']) await page.getByTestId(`hw-ack-${k}`).check()
  await shot(page, '06_grant_flight_permission.png')
  await page.getByTestId('hw-ack-weather_ok').check()
  await page.getByTestId('hw-grant-confirm').click()
  await expect(page.getByTestId('hw-permission-active')).toBeVisible()
  await shot(page, '07_flight_permitted.png', 'hw-safety')

  // ---------------------------------------------------------- 5 arm + take-off
  await page.getByTestId('mc-arm').click()
  await page.getByTestId('mc-confirm-mode').check()
  await shot(page, '08_arm_real_aircraft_confirmation.png')
  await page.getByTestId('mc-confirm-ok').click()
  await expect(page.getByTestId('mc-armed')).toHaveText('ARMED', { timeout: 20_000 })
  await expect(page.getByTestId('cmd-state-TAKEOFF').first()).toHaveAttribute('data-state', 'confirmed', { timeout: 40_000 })
  await shot(page, '09_airborne.png', 'mc-command-log')

  // ---------------------------------------------------------- 6 go-to + limits
  await page.getByTestId('mc-goto-pick').click()
  await clickMap(page, HOME[0] + 0.00025, HOME[1] + 0.0002)
  await page.getByTestId('mc-goto-send').click()
  await page.getByTestId('mc-confirm-mode').check()
  await page.getByTestId('mc-confirm-ok').click()
  await expect(page.getByTestId('cmd-state-GOTO').first()).toHaveAttribute('data-state', 'confirmed', { timeout: 40_000 })
  await shot(page, '10_goto_confirmed.png', 'mc-command-log')
  const far = await api(request, token, 'POST', `/mission-control/drones/${d1.id}/commands`,
    { command: 'GOTO', params: { latitude: HOME[0] + 0.003, longitude: HOME[1], altitude_m: 10 }, confirm_mode: 'hardware' })
  // Refused by whichever layer catches it first: the backend's geofence check
  // (ZONE_VIOLATION) or the bridge's 150 m hard limit (DISTANCE_LIMIT).
  const why = far.body.code ?? far.body.failure_reason ?? ''
  console.log('far GOTO refused:', why, far.body.detail ?? '')
  expect(why).toMatch(/ZONE_VIOLATION|DISTANCE_LIMIT/)
  await page.waitForTimeout(1000)
  await shot(page, '11_goto_beyond_limit_refused.png', 'mc-command-log')

  // ---------------------------------------------------------- 7 pilot override
  await request.post(`${PILOT}/pilot?sysid=1&mode=ALT_HOLD`)
  // No refresh click: the panel re-checks every 5 s and shows the override by itself.
  await expect(page.getByTestId('hw-override')).toBeVisible({ timeout: 15_000 })
  await page.getByTestId('mc-rtl').click()
  await page.getByTestId('mc-confirm-mode').check()
  await page.getByTestId('mc-confirm-ok').click()
  await expect(page.getByTestId('mc-cmd-error')).toContainText('PILOT_OVERRIDE')
  await shot(page, '12_pilot_override_locks_out.png', 'hw-override')
  await page.getByTestId('hw-release-reason').fill('Pilot handed control back after the takeover test')
  await page.getByTestId('hw-release').click()
  await expect(page.getByTestId('hw-override')).toHaveCount(0, { timeout: 15_000 })
  await page.getByTestId('mc-rtl').click()
  await page.getByTestId('mc-confirm-mode').check()
  await page.getByTestId('mc-confirm-ok').click()
  await expect(page.getByTestId('cmd-state-RTL').first()).toHaveAttribute('data-state', 'confirmed', { timeout: 40_000 })
  await shot(page, '13_released_then_rtl.png', 'mc-command-log')
  await expect(page.getByTestId('mc-armed')).toHaveText('DISARMED', { timeout: 120_000 })
  // Landed in RTL (not armable). The pilot resets the mode switch; Drone MatrX
  // sees a mode change it did not make, so the operator releases it again.
  await request.post(`${PILOT}/pilot?sysid=1&mode=STABILIZE`)
  await expect(page.getByTestId('hw-override')).toBeVisible({ timeout: 15_000 })
  await page.getByTestId('hw-release-reason').fill('Landed; pilot reset the mode switch to STABILIZE')
  await page.getByTestId('hw-release').click()
  await expect(page.getByTestId('hw-override')).toHaveCount(0, { timeout: 15_000 })

  // ---------------------------------------------------------- 8 video
  await page.getByTestId('mc-tab-video').click()
  await page.getByRole('button', { name: 'Refresh' }).click()
  await page.getByTestId('mc-layout-4').click().catch(() => undefined)
  await page.waitForTimeout(6000)
  await shot(page, '14_camera_feeds.png')

  // ---------------------------------------------------------- 9 CBBA mission on two drones
  const m = await api(request, token, 'POST', '/missions', { name: `Field survey ${new Date().toISOString().slice(11, 16)}`, mission_type: 'SURVEY', execution_mode: 'live' })
  const mid = m.body.id as string
  const k = 0.00025, n = 0.00018
  const aoi = { type: 'Polygon', coordinates: [[[HOME[1] - k, HOME[0] + 0.0001], [HOME[1] + k, HOME[0] + 0.0001], [HOME[1] + k, HOME[0] + 0.0001 + n], [HOME[1] - k, HOME[0] + 0.0001 + n], [HOME[1] - k, HOME[0] + 0.0001]]] }
  for (const [s, data] of [[1, { template: 'survey' }], [2, { source: 'drawn', geometry: aoi }]] as [number, unknown][])
    expect((await api(request, token, 'PUT', `/missions/${mid}/wizard/steps/${s}`, { data })).status).toBe(200)
  const wiz = (await api(request, token, 'GET', `/missions/${mid}/wizard`)).body
  expect((await api(request, token, 'PUT', `/missions/${mid}/wizard/steps/3`, { data: { ...wiz.config.flight, altitude_m: 10, speed_mps: 4 } })).status).toBe(200)
  expect((await api(request, token, 'PUT', `/missions/${mid}/wizard/steps/4`, { data: { required_sensors: ['camera'], drone_ids: [d1.id, d2.id] } })).status).toBe(200)
  expect((await api(request, token, 'PUT', `/missions/${mid}/wizard/steps/5`, { data: { min_battery_pct: 30, max_tasks_per_drone: 40 } })).status).toBe(200)
  const plan = await api(request, token, 'POST', `/missions/${mid}/cbba/allocate`, {})
  expect(plan.status).toBe(200)
  await page.goto(`/missions/${mid}/plan`)
  await page.waitForTimeout(3000)
  await shot(page, '15_cbba_plan_for_real_drones.png')

  await page.goto('/mission-control')
  for (const [d, alt] of [[d1, 10], [d2, 15]] as const) {
    await page.getByTestId(`mc-drone-${d.name}`).click()
    await page.getByTestId('hw-mission-select').selectOption(mid)
    await page.getByTestId('hw-mission-alt').fill(String(alt))
    await page.getByTestId('hw-mission-upload').click()
    await expect(page.getByTestId('hw-mission-uploaded')).toContainText(`${alt} m`, { timeout: 30_000 })
    if (d === d2) await shot(page, '16_mission_uploaded_per_drone_altitude.png', 'hw-mission')
    if (await page.getByTestId('hw-permission-none').isVisible()) { // drone 1 may still hold the earlier permission
      await page.getByTestId('hw-grant-open').click()
      for (const k2 of ['rc_pilot_ready', 'area_clear', 'observer_present', 'legal_ok', 'weather_ok']) await page.getByTestId(`hw-ack-${k2}`).check()
      await page.getByTestId('hw-grant-confirm').click()
    }
    await expect(page.getByTestId('hw-permission-active')).toBeVisible()
    await page.getByTestId('hw-mission-start').click()
    await page.getByTestId('mc-confirm-mode').check()
    await page.getByTestId('mc-confirm-ok').click()
    await expect(page.getByTestId('cmd-state-MISSION_START').first()).toHaveAttribute('data-state', 'confirmed', { timeout: 40_000 })
  }
  await centre(page, HOME[0] + 0.0002, HOME[1], 18)
  await page.waitForTimeout(8000)
  await shot(page, '17_two_drones_flying_cbba_mission.png')

  // ---------------------------------------------------------- 10 fleet command
  await page.getByTestId('mc-hold-all').click()
  await expect(page.getByTestId('mc-fleet-targets')).toBeVisible()
  await page.getByTestId('mc-fleet-confirm').click()
  for (const n of ['HW-01', 'HW-02'])  // the demo fleet is included too; check the real aircraft
    await expect(page.getByTestId(`mc-fleet-result-${n}`).locator('[data-state]')).toHaveAttribute('data-state', 'confirmed', { timeout: 60_000 })
  await shot(page, '18_hold_all_real_drones.png')
  await page.getByTestId('mc-fleet-close').click()
  await page.getByTestId('mc-rtl-all').click()
  await page.getByTestId('mc-fleet-confirm').click()
  for (const n of ['HW-01', 'HW-02'])
    await expect(page.getByTestId(`mc-fleet-result-${n}`).locator('[data-state]')).toHaveAttribute('data-state', 'confirmed', { timeout: 60_000 })
  await page.getByTestId('mc-fleet-close').click()
})
