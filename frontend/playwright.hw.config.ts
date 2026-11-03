import { defineConfig, devices } from '@playwright/test'

// Hardware workflow walkthrough against practice vehicles (see e2e/hardware-walkthrough.spec.ts).
// Expects backend, vite (5173), the hardware bridge and practice vehicles to be running already.
export default defineConfig({
  testDir: './e2e',
  testMatch: /(hardware-walkthrough|drone-setup-walkthrough)\.spec\.ts/,
  timeout: 15 * 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: 'line',
  use: {
    baseURL: 'http://localhost:5173',
    viewport: { width: 1600, height: 1000 },
    actionTimeout: 30_000,
    ...devices['Desktop Chrome'],
  },
  projects: [{ name: 'chromium', use: { viewport: { width: 1600, height: 1000 } } }],
})
