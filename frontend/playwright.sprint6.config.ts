import path from 'node:path'
import { defineConfig, devices } from '@playwright/test'

/**
 * Sprint 6 mission-control E2E against the real stack: backend + PostGIS +
 * NATS, simhost (Gazebo + PX4 SITL bench), MediaMTX (WebRTC). Chromium gets
 * a fake camera that shows the bench controller's binding QR code
 * (written by e2e/sprint6.setup.ts before the browser starts).
 *
 *   npx playwright test -c playwright.sprint6.config.ts
 */
const python = process.platform === 'win32' ? String.raw`venv\Scripts\python` : 'venv/bin/python'
const QR_Y4M = path.resolve(process.cwd(), 'test-results/sprint6/qr_camera.y4m')

export default defineConfig({
  testDir: './e2e',
  testMatch: /sprint6\.spec\.ts/,
  globalSetup: './e2e/sprint6.setup.ts',
  timeout: 30 * 60_000,
  expect: { timeout: 20_000 },
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:5173',
    viewport: { width: 1600, height: 1000 },
    trace: 'retain-on-failure',
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
    video: { mode: 'on', size: { width: 1280, height: 800 } },
    permissions: ['camera'],
    launchOptions: {
      args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-video-capture=${QR_Y4M}`,
        '--autoplay-policy=no-user-gesture-required'],
    },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1600, height: 1000 } } }],
  webServer: [
    { command: `${python} -m uvicorn app.main:app --port 8000`, cwd: '../backend', url: 'http://localhost:8000/health', reuseExistingServer: true, timeout: 60_000 },
    // A built app (no Vite dev client): the dev server's HMR client reloads the page when its
    // socket drops under load, which broke long runs. Development mode keeps the local API URL.
    { command: 'npm run build:e2e && npx vite preview --outDir dist-e2e --port 5173 --strictPort', url: 'http://localhost:5173', reuseExistingServer: false, timeout: 300_000 },
  ],
})
