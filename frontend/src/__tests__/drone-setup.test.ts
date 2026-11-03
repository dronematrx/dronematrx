import { describe, expect, it } from 'vitest'
import type { SetupView } from '../api/missionControl'
import {
  RECOMMENDED_MODES, STEPS, estimateCells, explainPrearm, motorCount, progress, safetyValues, stepStatus, suggestedSystemId,
} from '../components/hwsetup/setupSteps'

const FACTORY: Record<string, number> = {
  SYSID_THISMAV: 1, FRAME_CLASS: 0, FRAME_TYPE: 1, FS_GCS_ENABLE: 0, FS_THR_ENABLE: 1, BATT_MONITOR: 4, BATT_FS_LOW_ACT: 0, FENCE_ENABLE: 0,
  FLTMODE_CH: 5, FLTMODE1: 0, FLTMODE2: 0, FLTMODE3: 0, FLTMODE4: 0, FLTMODE5: 0, FLTMODE6: 0,
  RC1_MIN: 1100, RC1_MAX: 1900, RC1_TRIM: 1500, RC2_MIN: 1100, RC2_MAX: 1900, RC2_TRIM: 1500,
  RC3_MIN: 1100, RC3_MAX: 1900, RC3_TRIM: 1500, RC4_MIN: 1100, RC4_MAX: 1900, RC4_TRIM: 1500,
  INS_ACCOFFS_X: 0, INS_ACCOFFS_Y: 0, INS_ACCOFFS_Z: 0, INS_ACCSCAL_X: 1, INS_ACCSCAL_Y: 1, INS_ACCSCAL_Z: 1,
  COMPASS_USE: 1, COMPASS_OFS_X: 0, COMPASS_OFS_Y: 0, COMPASS_OFS_Z: 0,
}

function view(over: Partial<SetupView> = {}, params: Record<string, number> = {}): SetupView {
  return {
    fc_id: 'COM5-1', identity: 'serial:COM5/1', endpoint: 'serial:COM5', system_id: 1, firmware: 'ArduPilot v4.6.0', heartbeat_age_s: 0.4,
    bound: false, drone_id: null, uin: null, mode: 'STABILIZE', armed: false,
    params: { ...FACTORY, ...params }, battery_v: 16.4, battery_pct: 90, gps_fix: 3, satellites: 11, rc_channels: [1500, 1500, 1000, 1500],
    rc_age_s: 0.2, telemetry_radio: true, accelcal: { state: 'idle', position: null, message: null }, magcal: {},
    prearm_messages: ['PreArm: 3D Accel calibration needed', 'PreArm: Compass not calibrated'], messages: [], camera_url: null, link: null,
    other_system_ids: [], port: 'COM5', radio_netid: null, ...over,
  }
}

describe('drone setup steps', () => {
  it('a factory-new flight controller needs every required step', () => {
    const v = view({ other_system_ids: [1] })
    for (const id of ['identity', 'frame', 'radio', 'accel', 'compass', 'rc', 'modes', 'safety', 'check', 'register'] as const) {
      expect(stepStatus(id, v, false), id).toBe('todo')
    }
    expect(stepStatus('motors', v, false)).toBe('optional')
    expect(stepStatus('camera', v, false)).toBe('optional')
    expect(progress(v, false)).toEqual({ done: 0, required: 10 })
  })

  it('a fully configured drone is done', () => {
    const v = view({ system_id: 2, other_system_ids: [1], radio_netid: 26, camera_url: 'rtsp://x/live', prearm_messages: [] }, {
      SYSID_THISMAV: 2, FRAME_CLASS: 1, FS_GCS_ENABLE: 1, BATT_FS_LOW_ACT: 2, FENCE_ENABLE: 1, ...RECOMMENDED_MODES,
      RC1_MIN: 1012, RC1_MAX: 1988, RC2_MIN: 1010, RC2_MAX: 1990, RC3_MIN: 1005, RC3_MAX: 1995, RC3_TRIM: 1005,
      RC4_MIN: 1011, RC4_MAX: 1989, INS_ACCOFFS_X: 0.03, COMPASS_OFS_X: 40,
    })
    expect(STEPS.map((s) => stepStatus(s.id, v, true)).filter((s) => s === 'todo')).toEqual([])
    expect(progress(v, true)).toEqual({ done: 10, required: 10 })
    expect(progress({ ...v, telemetry_radio: false }, true)).toEqual({ done: 9, required: 9 }) // nothing to pair on USB
  })

  it('duplicate ids are detected and a free one suggested', () => {
    expect(suggestedSystemId(view({ other_system_ids: [1, 2, 4] }))).toBe(3)
    expect(stepStatus('identity', view({ other_system_ids: [2] }), false)).toBe('done')
  })

  it('USB and network links have no radio to pair', () => {
    expect(stepStatus('radio', view({ telemetry_radio: false }), false)).toBe('skip')
    expect(stepStatus('radio', view({ port: null }), false)).toBe('skip')
  })

  it('a calibration is not done while the autopilot still complains about it', () => {
    const v = view({}, { INS_ACCOFFS_X: 0.02 })
    expect(stepStatus('accel', v, false)).toBe('todo')
    expect(stepStatus('accel', { ...v, prearm_messages: [] }, false)).toBe('done')
    expect(stepStatus('compass', view({}, { COMPASS_USE: 0 }), false)).toBe('skip')
  })

  it('RC counts as calibrated only with a plausible stored range', () => {
    const cal = { RC1_MIN: 1012, RC1_MAX: 1988, RC1_TRIM: 1500, RC2_MIN: 1010, RC2_MAX: 1990, RC2_TRIM: 1500,
      RC3_MIN: 1005, RC3_MAX: 1995, RC3_TRIM: 1005, RC4_MIN: 1011, RC4_MAX: 1989, RC4_TRIM: 1500 }
    const ok = view({ prearm_messages: [] }, cal)
    expect(stepStatus('rc', ok, false)).toBe('done')
    expect(stepStatus('rc', view({ prearm_messages: [] }), false)).toBe('todo') // factory 1100/1900
    expect(stepStatus('rc', view({ prearm_messages: [] }, { ...cal, RC2_MAX: 1300 }), false)).toBe('todo') // range too small
    expect(stepStatus('rc', view({ prearm_messages: [] }, { ...cal, RC4_TRIM: 2100 }), false)).toBe('todo') // trim outside range
    expect(stepStatus('rc', { ...ok, prearm_messages: ['PreArm: RC3_MIN is greater than RC3_TRIM'] }, false)).toBe('todo')
  })

  it('flight modes need a recovery mode and RTL', () => {
    expect(stepStatus('modes', view({}, RECOMMENDED_MODES), false)).toBe('done')
    expect(stepStatus('modes', view({}, { FLTMODE1: 2, FLTMODE6: 5 }), false)).toBe('todo')
  })

  it('safety values: battery thresholds from the cell count, return height per drone', () => {
    expect(estimateCells(16.4)).toBe(4)
    expect(estimateCells(25.0)).toBe(6)
    expect(estimateCells(null)).toBeNull()
    const s = safetyValues(view({ system_id: 2 }), 4)
    expect(s).toMatchObject({ FS_GCS_ENABLE: 1, FS_THR_ENABLE: 1, BATT_FS_LOW_ACT: 2, FENCE_ENABLE: 1, RTL_ALT: 2000, BATT_LOW_VOLT: 14, BATT_CRT_VOLT: 13.2 })
    expect(safetyValues(view(), null)).not.toHaveProperty('BATT_LOW_VOLT')
  })

  it('motor count follows the frame', () => {
    expect(motorCount(view({}, { FRAME_CLASS: 2 }))).toBe(6)
    expect(motorCount(view({}, { FRAME_CLASS: 0 }))).toBe(4)
  })

  it('explains pre-arm messages in plain words', () => {
    expect(explainPrearm('PreArm: Compass not calibrated')).toMatch(/step 5/)
    expect(explainPrearm('PreArm: Need 3D Fix')).toMatch(/outdoors/)
    expect(explainPrearm('PreArm: Something new')).toBe('Something new')
  })
})
