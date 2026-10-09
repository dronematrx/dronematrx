import { expect, type Page } from '@playwright/test'

export const API = 'http://localhost:8000'
export const PASSWORD = 'DroneMatrx@2026'
export const BASE: [number, number] = [23.2156, 72.6369]

export async function login(page: Page, email = 'operator@dronematrx.com') {
  await page.goto('/login')
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign In' }).click()
  await expect(page).toHaveURL(/\/fleet/)
}

export async function createMission(page: Page, name: string) {
  await page.goto('/missions')
  await page.getByTestId('new-mission-name').fill(name)
  await page.getByTestId('create-mission').click()
  await expect(page).toHaveURL(/\/missions\/[0-9a-f-]+\/plan/)
  await expect(page.getByTestId('wizard-step-1')).toBeVisible()
}

/** Click the wizard map at a geographic position via the live Leaflet map. */
export async function clickMapAt(page: Page, lat: number, lng: number) {
  const container = page.locator('[data-testid="wizard-map"] .leaflet-container')
  const box = await container.boundingBox()
  if (!box) throw new Error('map not visible')
  const point = await page.evaluate(([la, ln]) => {
    const map = (window as unknown as { __dmMap: { latLngToContainerPoint: (p: [number, number]) => { x: number; y: number } } }).__dmMap
    return map.latLngToContainerPoint([la, ln])
  }, [lat, lng] as [number, number])
  expect(point.x).toBeGreaterThan(0)
  expect(point.y).toBeGreaterThan(0)
  expect(point.x).toBeLessThan(box.width)
  expect(point.y).toBeLessThan(box.height)
  await page.mouse.click(box.x + point.x, box.y + point.y)
}

/** Rectangle AOI from two opposite corners (centre +/- half degrees). */
export async function drawRectangleAoi(page: Page, centerLat: number, centerLng: number, half: number) {
  await page.getByTestId('draw-rectangle').click()
  await clickMapAt(page, centerLat - half, centerLng - half)
  await clickMapAt(page, centerLat + half, centerLng + half)
  await expect(page.getByTestId('aoi-vertex-count')).toContainText('4')
}

/** Steps 1-5 with defaults; leaves the browser on step 6 after validation. */
export async function planMission(page: Page, name: string, aoi: { lat: number; lng: number; half: number }, speed = '10') {
  await createMission(page, name)
  await page.getByTestId('template-survey').click()
  await page.getByTestId('wizard-next-1').click()

  await expect(page.getByTestId('wizard-step-2')).toBeVisible()
  await drawRectangleAoi(page, aoi.lat, aoi.lng, aoi.half)
  await page.getByTestId('wizard-next-2').click()

  await expect(page.getByTestId('wizard-step-3')).toBeVisible()
  await page.getByTestId('flight-speed').fill(speed)
  await page.getByTestId('flight-altitude').fill('70')
  await page.getByTestId('wizard-next-3').click()

  await expect(page.getByTestId('wizard-step-4')).toBeVisible()
  await page.getByTestId('sensor-camera').waitFor()
  await page.getByTestId('wizard-next-4').click()

  await expect(page.getByTestId('wizard-step-5')).toBeVisible()
  await page.getByTestId('constraint-min-battery').fill('35')
  await page.getByTestId('constraint-max-tasks').fill('25')
  await page.getByTestId('wizard-next-5').click()

  await expect(page.getByTestId('wizard-step-6')).toBeVisible()
  await expect(page.getByTestId('validation-result')).toBeVisible({ timeout: 30_000 })
}

/** Restore the simulated fleet to its seeded state (ADMIN API), so every
 * suite starts from the same drones/batteries regardless of earlier runs. */
export async function resetFleet(request: import('@playwright/test').APIRequestContext) {
  const login = await request.post(`${API}/auth/login`, { data: { email: 'admin@dronematrx.com', password: PASSWORD } })
  expect(login.ok()).toBeTruthy()
  const { access_token } = await login.json()
  const reset = await request.post(`${API}/orca/fleet/reset`, { headers: { Authorization: `Bearer ${access_token}` } })
  expect(reset.ok(), `fleet reset ${reset.status()}`).toBeTruthy()
  // Let a few telemetry cycles land so drone rows reflect the reset state.
  await new Promise((r) => setTimeout(r, 2500))
}
