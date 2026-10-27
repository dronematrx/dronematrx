/**
 * Sprint 6 E2E global setup (runs before the browser starts):
 *  - clean slate: stop running SITL benches, unbind controllers, remove the E2E NFZ;
 *  - start a SITL bench (3 PX4 x500 with downward cameras) and 9 test patterns;
 *  - write the real binding QR code of bench controller 02 into a Y4M file
 *    that Chromium uses as its (fake) camera, so the QR flow is exercised by
 *    an actual camera scan in the page.
 */
import fs from 'node:fs'
import path from 'node:path'
import QRCode from 'qrcode'

export const API = 'http://localhost:8000'
export const STATE_DIR = path.resolve(process.cwd(), 'test-results/sprint6')
export const QR_Y4M = path.join(STATE_DIR, 'qr_camera.y4m')
export const STATE_FILE = path.join(STATE_DIR, 'state.json')

async function call<T>(method: string, p: string, token?: string, body?: unknown, timeoutMs = 420_000): Promise<T> {
  const res = await fetch(`${API}${p}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  })
  const text = await res.text()
  if (!res.ok) throw new Error(`${method} ${p} -> ${res.status} ${text.slice(0, 300)}`)
  return (text ? JSON.parse(text) : undefined) as T
}

export async function login(email: string): Promise<string> {
  return (await call<{ access_token: string }>('POST', '/auth/login', undefined, { email, password: 'DroneMatrx@2026' })).access_token
}

/** QR modules -> 640x480 4:2:0 Y4M (white quiet zone), a few identical frames (Chromium loops the file). */
function qrToY4m(text: string, file: string) {
  const qr = QRCode.create(text, { errorCorrectionLevel: 'M' })
  const n = qr.modules.size
  const W = 640, H = 480
  const cell = Math.floor(Math.min(W, H) / (n + 8))
  const ox = Math.floor((W - n * cell) / 2), oy = Math.floor((H - n * cell) / 2)
  const y = Buffer.alloc(W * H, 235)
  for (let r = 0; r < n; r++)
    for (let c = 0; c < n; c++)
      if (qr.modules.get(r, c))
        for (let dy = 0; dy < cell; dy++) y.fill(16, (oy + r * cell + dy) * W + ox + c * cell, (oy + r * cell + dy) * W + ox + (c + 1) * cell)
  const uv = Buffer.alloc((W / 2) * (H / 2), 128)
  const frame = Buffer.concat([Buffer.from('FRAME\n'), y, uv, uv])
  fs.writeFileSync(file, Buffer.concat([Buffer.from(`YUV4MPEG2 W${W} H${H} F10:1 Ip A1:1 C420jpeg\n`), ...Array(10).fill(frame)]))
}

export default async function globalSetup() {
  fs.mkdirSync(STATE_DIR, { recursive: true })
  const op = await login('operator@dronematrx.com')
  const ov = await call<{ bench_sessions: Record<string, string> }>('GET', '/mission-control/overview', op)
  for (const [sid, st] of Object.entries(ov.bench_sessions)) if (st === 'ready' || st === 'running') await call('DELETE', `/mission-control/bench/${sid}`, op)
  for (const b of await call<{ id: string; state: string }[]>('GET', '/mission-control/fc/bindings', op))
    if (b.state === 'bound' || b.state === 'binding') await call('POST', `/mission-control/fc/bindings/${b.id}/unbind`, op).catch(() => undefined)
  const t0 = Date.now()
  const bench = await call<{ session_id: string; controllers: string[]; markers: { name: string; latitude: number; longitude: number }[]; startup_s: number }>(
    'POST', '/mission-control/bench', op, { count: 3, cameras: 1 })
  const benchWallS = (Date.now() - t0) / 1000
  // 1 SITL camera + 8 test patterns = nine distinct sources for the 3x3 layout.
  await call('POST', '/video/test-sources', op, { count: 8 })
  const fc2 = bench.controllers[1]
  const { qr_text } = await call<{ qr_text: string }>('GET', `/mission-control/fc/qr?fc_identity=${encodeURIComponent(`sitl:${bench.session_id}:${fc2}`)}&conn=sitl&sysid=2&ref=${fc2}`, op)
  qrToY4m(qr_text, QR_Y4M)
  fs.writeFileSync(STATE_FILE, JSON.stringify({ bench, benchWallS, qr_text, startedAt: new Date().toISOString() }, null, 2))
}
