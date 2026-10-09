import { expect, test } from '@playwright/test'
import { API, BASE, login, planMission, resetFleet } from './helpers'

test.beforeAll(async ({ request }) => resetFleet(request))

// Clear of every seeded geofence/NFZ, south-west of HQ (the NFZ is north-east).
const CLEAR_AOI = { lat: BASE[0] - 0.0105, lng: BASE[1] - 0.004, half: 0.0015 }
// Inside the seeded "Airport Approach NFZ".
const NFZ_AOI = { lat: BASE[0] + 0.012, lng: BASE[1] + 0.009, half: 0.002 }

test('critical path: wizard -> CBBA -> validate -> arm -> activate', async ({ page }) => {
  await login(page)
  const name = `E2E Survey ${Date.now()}`
  await planMission(page, name, CLEAR_AOI)

  // CBBA ran on step 6 and converged.
  await expect(page.getByTestId('cbba-status')).toContainText(/CBBA Converged/i)
  await expect(page.getByTestId('validation-result')).toContainText('PASSED')

  // Assignments + coloured lanes, one per drone, with ETA labels.
  const lanes = page.getByTestId('lane-card')
  await expect(lanes.first()).toBeVisible()
  const laneCount = await lanes.count()
  expect(laneCount).toBeGreaterThan(0)
  await expect(page.locator('path.dm-cbba-lane')).toHaveCount(laneCount)
  const strokes = await page.locator('path.dm-cbba-lane').evaluateAll((els) => els.map((e) => e.getAttribute('stroke')))
  expect(new Set(strokes).size).toBe(laneCount) // distinct colour per drone
  await expect(page.locator('.dm-lane-label').first()).toContainText(/ETA \d+:\d{2}/)
  await expect(page.getByTestId('lane-eta').first()).toContainText(/ETA \d+:\d{2}/)

  // Coverage and summary computed from the plan.
  await expect(page.getByTestId('summary-coverage')).toHaveText(/^\d+%$/)
  const coverage = Number((await page.getByTestId('summary-coverage').textContent())!.replace('%', ''))
  expect(coverage).toBeGreaterThan(50)
  await expect(page.getByTestId('summary-tasks')).toHaveText(/^(\d+)\/\1$/) // everything assigned

  // Research panel.
  await page.getByTestId('cbba-details-toggle').click()
  await expect(page.getByTestId('cbba-details')).toContainText('Iterations')

  // ETA updates live when speed changes (no reload).
  const before = await page.getByTestId('summary-duration').textContent()
  await page.getByTestId('stepper-3').click()
  await page.getByTestId('flight-speed').fill('15')
  await expect(page.getByTestId('summary-duration')).not.toHaveText(before!, { timeout: 15_000 })
  await page.getByTestId('wizard-next-3').click()
  await page.getByTestId('stepper-6').click()
  await page.getByTestId('validate-mission').click()
  await expect(page.getByTestId('validation-result')).toContainText('PASSED')

  // Resume after a refresh restores the saved mission state.
  await page.reload()
  await expect(page.getByTestId('wizard-step-6')).toBeVisible()
  await expect(page.getByTestId('summary-validation')).toHaveText('PASSED')

  // ARM -> ACTIVE through confirm dialogs (server re-checks everything).
  await page.getByTestId('arm-mission').click()
  await page.getByTestId('confirm-dialog-ok').click()
  await expect(page.locator('.dm-wizard-summary')).toContainText('armed')
  await page.getByTestId('activate-mission').click()
  await page.getByTestId('confirm-dialog-ok').click()
  await expect(page.locator('.dm-wizard-summary')).toContainText('active')
  await expect(page.getByTestId('complete-mission')).toBeVisible()
})

test('NFZ negative path: AOI through an NFZ blocks arming in UI and API', async ({ page }) => {
  await login(page)
  await planMission(page, `E2E NFZ ${Date.now()}`, NFZ_AOI)

  await expect(page.getByTestId('nfz-conflict-banner')).toBeVisible()
  await expect(page.getByTestId('nfz-conflict-banner')).toContainText(/NFZ Conflict/i)
  await expect(page.getByTestId('validation-result')).toContainText('FAILED')
  await expect(page.getByTestId('check-NFZ_CONFLICT').first()).toContainText('Airport Approach NFZ')
  await expect(page.locator('path.dm-nfz-path').first()).toBeVisible() // conflict overlay on the map
  await expect(page.getByTestId('arm-mission')).toBeDisabled()

  // Bypassing the UI does not help: the backend rejects the transition.
  const missionId = page.url().match(/missions\/([0-9a-f-]+)\/plan/)![1]
  const token = await page.evaluate(() => localStorage.getItem('dronematrx.access_token'))
  const resp = await page.request.post(`${API}/missions/${missionId}/transition`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { target_state: 'armed' },
  })
  expect(resp.status()).toBe(422)
  expect((await resp.json()).report.nfz_conflict).toBe(true)
  await page.reload()
  await expect(page.locator('.dm-wizard-summary')).toContainText('validating')
})

test('save & exit then resume restores wizard progress', async ({ page }) => {
  await login(page)
  const name = `E2E Resume ${Date.now()}`
  await page.goto('/missions')
  await page.getByTestId('new-mission-name').fill(name)
  await page.getByTestId('create-mission').click()
  await page.getByTestId('template-search').click()
  await page.getByTestId('wizard-next-1').click()
  await page.getByTestId('draw-rectangle').click()
  const { clickMapAt } = await import('./helpers')
  await clickMapAt(page, CLEAR_AOI.lat - 0.001, CLEAR_AOI.lng - 0.001)
  await clickMapAt(page, CLEAR_AOI.lat + 0.001, CLEAR_AOI.lng + 0.001)
  await page.getByTestId('wizard-save-exit').click()
  await expect(page).toHaveURL(/\/missions$/)

  await page.getByTestId('mission-row').filter({ hasText: name }).dblclick()
  await expect(page.getByTestId('wizard-step-3')).toBeVisible() // resumes at the next step
  await expect(page.getByTestId('flight-altitude')).toHaveValue('40') // "search" template default
  await page.getByTestId('wizard-back').click()
  await expect(page.getByTestId('wizard-step-2')).toBeVisible()
})

test('observer gets a read-only wizard', async ({ page }) => {
  await login(page, 'observer@dronematrx.com')
  await page.goto('/missions')
  await expect(page.getByTestId('create-mission')).toHaveCount(0)
  await page.getByTestId('mission-row').filter({ hasText: 'Solar Farm Survey' }).dblclick()
  await expect(page.getByTestId('wizard-step-6')).toBeVisible()
  await expect(page.getByTestId('arm-mission')).toHaveCount(0)
  await expect(page.getByTestId('run-cbba')).toHaveCount(0)
  await expect(page.getByTestId('lane-card').first()).toBeVisible()
})
