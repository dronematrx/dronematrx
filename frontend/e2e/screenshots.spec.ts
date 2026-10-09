import { expect, test } from '@playwright/test'
import { login } from './helpers'

/** Visual capture for review/docs. Skipped unless SCREENSHOTS=1. */
test.skip(!process.env.SCREENSHOTS, 'set SCREENSHOTS=1 to capture UI screenshots')

const OUT = process.env.SCREENSHOT_DIR ?? '../documents/screenshots/sprint3'

test('capture sprint 3 screens', async ({ page }) => {
  await login(page)
  await page.goto('/missions')
  await expect(page.getByTestId('mission-row').first()).toBeVisible()
  await page.screenshot({ path: `${OUT}/11-missions-list.png` })

  for (const [name, file] of [
    ['Solar Farm Survey', '12-wizard-step6-cbba-converged.png'],
    ['Airport Approach Survey', '13-wizard-nfz-conflict.png'],
    ['Lidar Corridor Inspection', '14-wizard-unassigned-task.png'],
  ] as const) {
    await page.goto('/missions')
    await page.getByTestId('mission-row').filter({ hasText: name }).first().dblclick()
    await expect(page.getByTestId('wizard-step-6')).toBeVisible()
    await page.waitForTimeout(1500)
    await page.screenshot({ path: `${OUT}/${file}` })
  }

  await page.getByTestId('stepper-3').click()
  await page.waitForTimeout(800)
  await page.screenshot({ path: `${OUT}/15-wizard-step3-flight.png` })
  await page.getByTestId('stepper-4').click()
  await page.waitForTimeout(800)
  await page.screenshot({ path: `${OUT}/16-wizard-step4-sensors.png` })

  await page.goto('/settings')
  await page.waitForTimeout(1500)
  await page.screenshot({ path: `${OUT}/17-geofence-library.png` })
  await page.goto('/map')
  await page.waitForTimeout(1500)
  await page.screenshot({ path: `${OUT}/18-flight-plan-geofence-types.png` })
})
