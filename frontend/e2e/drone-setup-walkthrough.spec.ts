/**
 * Guided Drone Setup walkthrough (screenshots for the setup guide).
 *
 * Runs the REAL backend, bridge (started from the page) and UI against two
 * PRACTICE vehicles started factory-fresh (hwbridge/tools/practice_vehicle.py
 * --fresh: both with system id 1, uncalibrated, failsafes off). Prerequisites:
 * backend + vite running, no bridge running, practice vehicles with --fresh.
 *
 *   npx playwright test -c playwright.hw.config.ts drone-setup
 */
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import path from 'node:path'
import { API, login, PASSWORD } from './helpers'

const SHOTS = path.resolve(process.cwd(), '../documents/sprint6-1/screenshots')
const PILOT = 'http://127.0.0.1:5799'

async function shot(page: Page, name: string, focus?: string) {
  if (focus) await page.getByTestId(focus).first().scrollIntoViewIfNeeded().catch(() => undefined)
  await page.waitForTimeout(600)
  await page.screenshot({ path: path.join(SHOTS, name), fullPage: false })
}

async function api(request: APIRequestContext, token: string, method: string, p: string, body?: unknown) {
  const r = await request.fetch(`${API}${p}`, { method, headers: { Authorization: `Bearer ${token}` }, data: body })
  return { status: r.status(), body: await r.json().catch(() => null) }
}

async function step(page: Page, id: string) {
  await page.getByTestId(`setup-step-${id}`).click()
  await expect(page.getByTestId(`setup-panel-${id}`)).toBeVisible()
}

async function done(page: Page, id: string, timeout = 20_000) {
  await expect(page.getByTestId(`setup-step-${id}`)).toHaveAttribute('data-status', 'done', { timeout })
}

test('guided drone setup walkthrough', async ({ page, request }) => {
  test.setTimeout(10 * 60_000)
  const token = (await (await request.post(`${API}/auth/login`, { data: { email: 'operator@dronematrx.com', password: PASSWORD } })).json()).access_token as string
  // Clean slate: unlink controllers from earlier runs.
  for (const b of (await api(request, token, 'GET', '/mission-control/fc/bindings')).body as { id: string; endpoint: string; state: string }[])
    if (b.endpoint.startsWith('tcp:127.0.0.1:57') && b.state !== 'unbound') await api(request, token, 'POST', `/mission-control/fc/bindings/${b.id}/unbind`)
  await request.post(`${PILOT}/sticks?sysid=1&mode=center`)

  await login(page)
  await page.goto('/drone-setup')

  // 1. Start the connection service from the page
  await expect(page.getByTestId('hw-bridge-start')).toBeVisible()
  await shot(page, 'S01_setup_start_service.png')
  await page.getByTestId('hw-bridge-start').click()
  await expect(page.getByTestId('hw-bridge-running')).toBeVisible({ timeout: 30_000 })

  // 2. Connect (practice vehicles listen on TCP; real radios are the COM-port rows)
  await page.locator('summary', { hasText: 'Network connection' }).click()
  for (const port of [5760, 5770]) {
    await page.getByTestId('hw-network-endpoint').fill(`tcp:127.0.0.1:${port}`)
    await page.getByTestId('hw-network-connect').click()
    await page.waitForTimeout(800)
  }
  await expect(page.getByTestId('hw-drone-tcp5760-1')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('hw-drone-tcp5770-1')).toBeVisible({ timeout: 20_000 })
  await shot(page, 'S02_setup_connect_radios.png', 'hw-setup-ports')
  await shot(page, 'S03_setup_drones_found_duplicate_id.png', 'hw-setup-drones')

  // Drone on 5770: give it ID 2
  await page.getByTestId('hw-drone-tcp5770-1').click()
  await expect(page.getByTestId('drone-setup')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('setup-step-identity')).toHaveAttribute('data-status', 'todo')
  await page.getByTestId('setup-sysid-input').fill('2')
  await shot(page, 'S04_setup_unique_id.png', 'drone-setup')
  await page.getByTestId('setup-sysid-apply').click()
  await expect(page.getByTestId('hw-drone-tcp5770-2')).toBeVisible({ timeout: 30_000 })
  await page.getByTestId('hw-drone-tcp5770-2').click()
  await done(page, 'identity', 30_000)

  // Frame
  await step(page, 'frame')
  await page.getByTestId('setup-frame-class').selectOption('1')
  await page.getByTestId('setup-frame-apply').click()
  await done(page, 'frame')
  await shot(page, 'S05_setup_frame.png', 'drone-setup')

  // Radio pairing: not needed on a network link
  await step(page, 'radio')
  await expect(page.getByTestId('setup-step-radio')).toHaveAttribute('data-status', 'skip')

  // Accelerometer: six positions
  await step(page, 'accel')
  await page.getByTestId('setup-accel-start').click()
  await expect(page.getByTestId('setup-accel-position')).toHaveText('LEVEL', { timeout: 15_000 })
  await shot(page, 'S06_setup_accel_position.png', 'drone-setup')
  for (const pos of ['LEVEL', 'LEFT', 'RIGHT', 'NOSE DOWN', 'NOSE UP', 'BACK']) {
    await expect(page.getByTestId('setup-accel-position')).toHaveText(pos, { timeout: 15_000 })
    await expect(page.getByTestId('setup-accel-next')).toBeEnabled({ timeout: 15_000 })
    await page.getByTestId('setup-accel-next').click()
  }
  await expect(page.getByTestId('setup-accel-result')).toBeVisible({ timeout: 15_000 })
  await shot(page, 'S07_setup_accel_done.png', 'drone-setup')

  // Compass
  await step(page, 'compass')
  await page.getByTestId('setup-compass-start').click()
  await expect(page.getByTestId('setup-compass-0')).toBeVisible({ timeout: 15_000 })
  await page.waitForTimeout(1500)
  await shot(page, 'S08_setup_compass_progress.png', 'drone-setup')
  await expect(page.getByTestId('setup-compass-0')).toContainText('SUCCESS', { timeout: 30_000 })
  await done(page, 'accel', 45_000) // old pre-arm warnings expire (ArduPilot repeats live ones every 30 s)

  // RC: the practice "pilot" moves the sticks through their range
  await step(page, 'rc')
  await page.getByTestId('setup-rc-start').click()
  await request.post(`${PILOT}/sticks?sysid=2&mode=sweep`)
  await expect(page.getByTestId('setup-rc-save')).toBeEnabled({ timeout: 30_000 })
  await shot(page, 'S09_setup_rc_calibration.png', 'drone-setup')
  await request.post(`${PILOT}/sticks?sysid=2&mode=center`)
  await page.waitForTimeout(2500)
  await page.getByTestId('setup-rc-save').click()
  await done(page, 'rc')

  // Flight modes
  await step(page, 'modes')
  await page.getByTestId('setup-modes-apply').click()
  await done(page, 'modes')
  await shot(page, 'S10_setup_flight_modes.png', 'drone-setup')

  // Safety
  await step(page, 'safety')
  await page.getByTestId('setup-safety-apply').click()
  await done(page, 'safety')
  await shot(page, 'S11_setup_safety.png', 'drone-setup')

  // Motor test (props off)
  await step(page, 'motors')
  await expect(page.getByTestId('setup-motor-1')).toBeDisabled()
  await page.getByTestId('setup-props-off').check()
  await page.getByTestId('setup-motor-1').click()
  await expect(page.getByTestId('setup-notice')).toContainText('Motor A spun')
  await shot(page, 'S12_setup_motor_test.png', 'drone-setup')

  // Camera
  await step(page, 'camera')
  await page.getByTestId('setup-camera-url').fill('rtsp://dmx-backend:dmx-backend-dev@127.0.0.1:8554/test/02/hi')
  await page.getByTestId('setup-camera-save').click()
  await done(page, 'camera')

  // Reboot & check
  await step(page, 'check')
  await page.getByTestId('setup-reboot').click()
  await expect(page.getByTestId('setup-check-ok')).toBeVisible({ timeout: 60_000 })
  await done(page, 'compass', 60_000)
  await shot(page, 'S13_setup_final_check.png', 'drone-setup')

  // Add to Drone MatrX
  await step(page, 'register')
  const suffix = String(Date.now() % 100000)
  await page.getByTestId('setup-register-name').fill(`SETUP-02-${suffix}`)
  await page.getByTestId('setup-register-uin').fill(`DMX-SETUP-${suffix}`)
  await page.getByTestId('setup-register-apply').click()
  await expect(page.getByTestId('setup-registered')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('setup-progress')).toHaveText(/^(\d+) of \1 required steps done$/, { timeout: 30_000 })
  await shot(page, 'S14_setup_registered_all_done.png', 'drone-setup')
})
