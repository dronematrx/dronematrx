import { describe, expect, it } from 'vitest'
import { errorMessage } from '../api/client'
import type { Drone } from '../api/types'
import { closedRing, formatDistance, formatDuration, formatPct, rectangleFromCorners } from '../lib/format'
import { polygonBounds, ringToLatLng } from '../lib/mapGeometry'
import { LANE_TOKENS, laneToken } from '../lib/tokens'
import { hasErrors, validateAoi, validateConstraints, validateFlight, validateSensors } from '../lib/wizardValidation'

const drone = (id: string, payloads: { type: string; status: string }[]): Drone =>
  ({ id, name: id, active: true, status: 'online', battery: 90, payload_summary: payloads.map((p) => ({ ...p, name: p.type })) }) as unknown as Drone

describe('lane palette', () => {
  it('is deterministic and wraps after 8 lanes', () => {
    expect(laneToken(0)).toBe('--dm-lane-1')
    expect(laneToken(7)).toBe('--dm-lane-8')
    expect(laneToken(8)).toBe(laneToken(0))
    expect(new Set(Array.from({ length: 8 }, (_, i) => laneToken(i))).size).toBe(LANE_TOKENS.length)
  })
})

describe('formatting', () => {
  it('formats durations, distances and percentages', () => {
    expect(formatDuration(65)).toBe('1:05')
    expect(formatDuration(3725)).toBe('1h 02m')
    expect(formatDuration(null)).toBe('--')
    expect(formatDistance(850)).toBe('850 M')
    expect(formatDistance(1530)).toBe('1.53 KM')
    expect(formatPct(72.4)).toBe('72%')
  })

  it('builds closed GeoJSON rings and rectangles', () => {
    expect(closedRing([[23.2, 72.6], [23.2, 72.7], [23.3, 72.7]])).toEqual([[72.6, 23.2], [72.7, 23.2], [72.7, 23.3], [72.6, 23.2]])
    const rect = rectangleFromCorners([23.3, 72.7], [23.2, 72.6])
    expect(rect).toEqual([[23.2, 72.6], [23.2, 72.7], [23.3, 72.7], [23.3, 72.6]])
    expect(ringToLatLng([[72.6, 23.2]])).toEqual([[23.2, 72.6]])
    expect(polygonBounds([[72.6, 23.2], [72.7, 23.3]])).toEqual([[23.2, 72.6], [23.3, 72.7]])
  })
})

describe('wizard step validation', () => {
  it('requires 3 AOI vertices', () => {
    expect(hasErrors(validateAoi([[1, 1], [1, 2]]))).toBe(true)
    expect(hasErrors(validateAoi([[1, 1], [1, 2], [2, 2]]))).toBe(false)
  })

  it('enforces flight parameter ranges', () => {
    const ok = { altitude_m: 60, speed_mps: 10, overlap_pct: 70, rth_battery_pct: 25, priority: 2, heading_deg: null }
    expect(validateFlight(ok)).toEqual({})
    expect(Object.keys(validateFlight({ ...ok, altitude_m: 500, speed_mps: 0, overlap_pct: 95 })).sort()).toEqual(['altitude_m', 'overlap_pct', 'speed_mps'])
    expect(validateFlight({ ...ok, heading_deg: 360 }).heading_deg).toBeDefined()
    expect(validateFlight({ ...ok, speed_mps: Number.NaN }).speed_mps).toBeDefined()
  })

  it('checks sensor compatibility, ignoring faulted payloads', () => {
    const cam = drone('cam', [{ type: 'camera', status: 'calibrated' }])
    const brokenThermal = drone('therm', [{ type: 'thermal', status: 'fault' }])
    expect(validateSensors(['camera'], [cam], {})).toEqual({})
    expect(validateSensors(['thermal'], [cam, brokenThermal], {}).sensor_thermal).toMatch(/No selected drone/)
    expect(validateSensors(['camera'], [], {}).drone_ids).toBeDefined()
    // A drone restricted to another payload no longer counts for camera.
    const dual = drone('dual', [{ type: 'camera', status: 'calibrated' }, { type: 'thermal', status: 'calibrated' }])
    expect(validateSensors(['camera'], [dual], { dual: 'thermal' }).sensor_camera).toBeDefined()
  })

  it('keeps launch minimum above the RTH reserve', () => {
    const base = { min_battery_pct: 40, max_tasks_per_drone: 15, max_flight_time_min: null, max_range_km: null }
    expect(validateConstraints(base, { rth_battery_pct: 25 })).toEqual({})
    expect(validateConstraints({ ...base, min_battery_pct: 20 }, { rth_battery_pct: 25 }).min_battery_pct).toMatch(/RTH reserve/)
    expect(validateConstraints({ ...base, max_tasks_per_drone: 0 }).max_tasks_per_drone).toBeDefined()
  })
})

describe('API error parsing', () => {
  it('reads string, list and Sprint 3 error bodies', () => {
    expect(errorMessage({ detail: 'NFZ conflict blocks arming', code: 'TRANSITION_BLOCKED' }, 'x')).toBe('NFZ conflict blocks arming')
    expect(errorMessage({ detail: [{ loc: ['body', 'data', 'altitude_m'], msg: 'too high' }] }, 'x')).toBe('altitude_m: too high')
    expect(errorMessage({ detail: 'Validation error', errors: [] }, 'x')).toBe('Validation error')
    expect(errorMessage(undefined, 'fallback')).toBe('fallback')
  })
})
