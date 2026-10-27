import { describe, expect, it } from 'vitest'
import { decodeBarcode, BARCODE_BITS } from '../lib/videoBarcode'
import { ageLabel, liveFleet, liveSummary } from '../components/missioncontrol/liveFleet'
import type { McDrone, McOverview } from '../api/missionControl'
import type { Drone } from '../api/types'

/** Mirror of sim/simhost/video.py encode_barcode (480 px reference frame). */
function frameWithBarcode(ms: number, counter: number, width = 480, height = 40): Uint8ClampedArray {
  const px = new Uint8ClampedArray(width * height * 4)
  const scale = width / 480
  for (let i = 0; i < BARCODE_BITS; i++) {
    const bit = i < 48 ? Math.floor(ms / 2 ** i) % 2 : Math.floor(counter / 2 ** (i - 48)) % 2
    if (!bit) continue
    for (let y = Math.floor(8 * scale); y < Math.floor(24 * scale); y++)
      for (let x = Math.floor((8 + i * 6) * scale); x < Math.floor((8 + (i + 1) * 6) * scale); x++) {
        const o = (y * width + x) * 4
        px[o] = px[o + 1] = px[o + 2] = 255
      }
  }
  return px
}

describe('test-pattern barcode', () => {
  it('decodes capture time and frame counter', () => {
    expect(decodeBarcode(frameWithBarcode(1792985052530, 579), 480)).toEqual({ captureMs: 1792985052530, counter: 579 })
  })
  it('decodes the half-size (lo) rendition', () => {
    expect(decodeBarcode(frameWithBarcode(1792985052530, 7, 240, 20), 240)).toEqual({ captureMs: 1792985052530, counter: 7 })
  })
  it('rejects frames without a plausible timestamp', () => {
    expect(decodeBarcode(new Uint8ClampedArray(480 * 40 * 4), 480)).toBeNull()
    expect(decodeBarcode(new Uint8ClampedArray(480 * 40 * 4).fill(255), 480)).toBeNull()
  })
})

function meta(id: string, lastIso: string | null, extra: Partial<McDrone> = {}): McDrone {
  return {
    id, name: id, uin: null, model: 'x500', simulated: true, telemetry_source: 'sitl', connection_state: 'connected', telemetry_age_s: 0,
    last_telemetry_at: lastIso, status: 'online', flight_mode: 'HOLD', armed: false, battery: 90, latitude: 23.24, longitude: 72.65,
    altitude: 0, heading: 0, speed: 0, gps_status: '3D_FIX', binding: null, mission: null, capabilities: [], ...extra,
  }
}

describe('live fleet freshness', () => {
  const t0 = Date.parse('2026-10-26T03:00:00Z')
  const overview = (drones: McDrone[]): McOverview => ({
    server_time: new Date(t0).toISOString(), stale_after_s: 5, offline_after_s: 60, drones, bench_sessions: {}, simhost: 'ok',
    detections_pending: 0, role: 'OPERATOR',
    summary: { total: 0, connected: 0, stale: 0, offline: 0, available: 0, on_mission: 0, failed: 0, by_source: { sitl: 0, hardware: 0, kinematic_demo: 0, none: 0 } },
  })

  it('goes stale and offline as telemetry ages, on the server clock', () => {
    const o = overview([meta('a', new Date(t0 - 1000).toISOString())])
    const clientSkew = 30_000 // the browser clock is 30 s ahead: must not matter
    const at = (msAfter: number) => liveFleet(o, t0 + clientSkew, [], t0 + clientSkew + msAfter)[0].connection_state
    expect(at(0)).toBe('connected')
    expect(at(4500)).toBe('stale')
    expect(at(70_000)).toBe('offline')
  })

  it('takes newer WebSocket telemetry over the REST snapshot, never older', () => {
    const o = overview([meta('a', new Date(t0 - 3000).toISOString(), { latitude: 1 })])
    const newer = { id: 'a', last_telemetry_at: new Date(t0).toISOString(), latitude: 2, longitude: 3, altitude: 10, heading: 5, speed: 1,
      battery: 80, flight_mode: 'MISSION', armed: true, status: 'active', gps_status: '3D_FIX' } as unknown as Drone
    expect(liveFleet(o, t0, [newer], t0)[0].latitude).toBe(2)
    const older = { ...newer, last_telemetry_at: new Date(t0 - 9000).toISOString(), latitude: 9 } as Drone
    expect(liveFleet(o, t0, [older], t0)[0].latitude).toBe(1)
  })

  it('reports no telemetry rather than inventing a state', () => {
    const d = liveFleet(overview([meta('a', null)]), t0, [], t0)[0]
    expect(d.connection_state).toBe('no_telemetry')
    expect(ageLabel(d.ageMs)).toBe('never')
  })

  it('summarises connected / stale / failed', () => {
    const o = overview([
      meta('a', new Date(t0).toISOString()),
      meta('b', new Date(t0 - 20_000).toISOString()),
      meta('c', null, { binding: { state: 'connection_failed' } as McDrone['binding'] }),
    ])
    const s = liveSummary(liveFleet(o, t0, [], t0))
    expect(s).toMatchObject({ total: 3, connected: 1, stale: 1, offline: 1, failed: 1 })
  })
})
