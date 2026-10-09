import { expect, test } from '@playwright/test'
import { BASE, login, planMission, resetFleet } from './helpers'

test.beforeAll(async ({ request }) => resetFleet(request))

/**
 * Offline-first check: every request that leaves the machine (OSM map tiles,
 * Google Fonts, anything else) is blocked. Mission planning, CBBA, PostGIS
 * validation and the state machine must keep working against the local
 * backend + database only.
 */
test('mission planning and CBBA work with the external network cut off', async ({ page }) => {
  const blocked: string[] = []
  await page.context().route('**/*', (route) => {
    const url = new URL(route.request().url())
    if (url.hostname === 'localhost' || url.hostname === '127.0.0.1') return route.continue()
    blocked.push(url.hostname)
    return route.abort('internetdisconnected')
  })

  await login(page)
  await planMission(page, `E2E Offline ${Date.now()}`, { lat: BASE[0] - 0.0105, lng: BASE[1] - 0.004, half: 0.0015 })

  await expect(page.getByTestId('cbba-status')).toContainText(/CBBA Converged/i)
  await expect(page.getByTestId('validation-result')).toContainText('PASSED')
  await expect(page.getByTestId('lane-card').first()).toBeVisible()
  await page.getByTestId('arm-mission').click()
  await page.getByTestId('confirm-dialog-ok').click()
  await expect(page.locator('.dm-wizard-summary')).toContainText('armed')

  // Only map tiles / fonts were lost -- documented as the remaining online dependency.
  const external = [...new Set(blocked)]
  expect(external.length).toBeGreaterThan(0)
  expect(external.every((h) => /openstreetmap\.org$|fonts\.(googleapis|gstatic)\.com$/.test(h))).toBe(true)
})
