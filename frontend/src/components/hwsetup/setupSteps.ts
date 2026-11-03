import type { SetupView } from '../../api/missionControl'

/**
 * The guided drone setup, in order. Each step's status is derived only from
 * what the flight controller reports (parameters, live state), never from what
 * the page remembers, so reopening the page shows the true state.
 */
export type StepId = 'identity' | 'frame' | 'radio' | 'accel' | 'compass' | 'rc' | 'modes' | 'safety' | 'motors' | 'camera' | 'check' | 'register'
export type StepStatus = 'done' | 'todo' | 'optional' | 'skip'

export const STEPS: { id: StepId; title: string; summary: string }[] = [
  { id: 'identity', title: 'Unique drone ID', summary: 'Every drone needs its own MAVLink ID so Drone MatrX can tell them apart.' },
  { id: 'frame', title: 'Frame type', summary: 'Tell the autopilot how many motors the drone has and how they are arranged.' },
  { id: 'radio', title: 'Telemetry radio pairing', summary: 'Give each radio pair its own network ID so pairs never mix their data.' },
  { id: 'accel', title: 'Accelerometer calibration', summary: 'Hold the drone in six positions so it knows which way is level.' },
  { id: 'compass', title: 'Compass calibration', summary: 'Rotate the drone in every direction so it knows where north is.' },
  { id: 'rc', title: 'RC transmitter calibration', summary: 'Move every stick to its limits so the drone knows the full range.' },
  { id: 'modes', title: 'Flight mode switch', summary: "Put the safety pilot's recovery modes on the transmitter switch." },
  { id: 'safety', title: 'Safety settings', summary: 'Return home on lost link, lost RC or low battery; keep the drone inside a fence.' },
  { id: 'motors', title: 'Motor test', summary: 'Spin each motor briefly (propellers OFF) to check order and direction.' },
  { id: 'camera', title: 'Camera', summary: "Add the drone camera's video address so it appears on the video wall." },
  { id: 'check', title: 'Reboot & final check', summary: 'Restart the autopilot and confirm it reports no pre-arm problems.' },
  { id: 'register', title: 'Add to Drone MatrX', summary: 'Register the drone as a real aircraft and link it to this flight controller.' },
]

export const MODE_NAMES: Record<number, string> = {
  0: 'STABILIZE', 2: 'ALT_HOLD', 3: 'AUTO', 4: 'GUIDED', 5: 'LOITER', 6: 'RTL', 9: 'LAND', 16: 'POSHOLD', 17: 'BRAKE', 21: 'SMART_RTL',
}
/** Switch low = ALT_HOLD, middle = LOITER, high = RTL (the 6 slots of a 3-position switch). */
export const RECOMMENDED_MODES: Record<string, number> = { FLTMODE1: 2, FLTMODE2: 2, FLTMODE3: 5, FLTMODE4: 5, FLTMODE5: 6, FLTMODE6: 6 }

export const FRAME_CLASSES: { value: number; label: string; motors: number }[] = [
  { value: 1, label: 'Quad (4 motors)', motors: 4 },
  { value: 2, label: 'Hexa (6 motors)', motors: 6 },
  { value: 3, label: 'Octa (8 motors)', motors: 8 },
  { value: 4, label: 'OctaQuad (8 motors)', motors: 8 },
  { value: 5, label: 'Y6 (6 motors)', motors: 6 },
  { value: 7, label: 'Tri (3 motors)', motors: 3 },
]
export const FRAME_TYPES: { value: number; label: string }[] = [
  { value: 1, label: 'X (most common)' },
  { value: 0, label: 'Plus (+)' },
  { value: 2, label: 'V' },
  { value: 3, label: 'H' },
]

const p = (v: SetupView, name: string) => v.params[name] ?? null
const isSet = (x: number | null, dflt: number) => x !== null && Math.abs(x - dflt) > 1e-6

export function motorCount(v: SetupView): number {
  return FRAME_CLASSES.find((f) => f.value === p(v, 'FRAME_CLASS'))?.motors ?? 4
}

/** Smallest id from 1 that no other connected drone uses. */
export function suggestedSystemId(v: SetupView): number {
  let id = 1
  while (v.other_system_ids.includes(id)) id++
  return id
}

/** Cell count from the pack voltage (3.3-4.3 V per cell). */
export function estimateCells(voltage: number | null): number | null {
  if (!voltage || voltage < 3) return null
  for (let cells = 1; cells <= 14; cells++) {
    const perCell = voltage / cells
    if (perCell <= 4.3 && perCell >= 3.3) return cells
  }
  return null
}

export function safetyValues(v: SetupView, cells: number | null): Record<string, number> {
  const out: Record<string, number> = {
    FS_GCS_ENABLE: 1, FS_GCS_TIMEOUT: 5, FS_THR_ENABLE: 1,
    BATT_FS_LOW_ACT: 2, BATT_FS_CRT_ACT: 1,
    FENCE_ENABLE: 1, FENCE_TYPE: 7, FENCE_ACTION: 1, FENCE_ALT_MAX: 40, FENCE_RADIUS: 150,
    // Different return heights per drone (15 m, 20 m, 25 m...) so returning drones don't meet.
    RTL_ALT: 1500 + 500 * Math.max(0, Math.min(10, v.system_id - 1)),
  }
  if (cells) {
    out.BATT_LOW_VOLT = Math.round(cells * 3.5 * 100) / 100
    out.BATT_CRT_VOLT = Math.round(cells * 3.3 * 100) / 100
  }
  return out
}

export function rcCalibrated(v: SetupView): boolean {
  // A new flight controller has every RCn_MIN/MAX at the defaults 1100 / 1900.
  return [1, 2, 3, 4].every((ch) => isSet(p(v, `RC${ch}_MIN`), 1100) || isSet(p(v, `RC${ch}_MAX`), 1900))
}

export function stepStatus(id: StepId, v: SetupView, bound: boolean): StepStatus {
  switch (id) {
    case 'identity':
      return v.system_id >= 1 && v.system_id <= 250 && !v.other_system_ids.includes(v.system_id) ? 'done' : 'todo'
    case 'frame':
      return (p(v, 'FRAME_CLASS') ?? 0) > 0 ? 'done' : 'todo'
    case 'radio':
      if (!v.port || !v.telemetry_radio) return 'skip' // USB cable or network link: nothing to pair
      return v.radio_netid !== null ? 'done' : 'todo'
    case 'accel': {
      const offs = ['X', 'Y', 'Z'].some((a) => isSet(p(v, `INS_ACCOFFS_${a}`), 0))
      const scal = ['X', 'Y', 'Z'].some((a) => isSet(p(v, `INS_ACCSCAL_${a}`), 1))
      return (offs || scal) && !v.prearm_messages.some((m) => /accel/i.test(m)) ? 'done' : 'todo'
    }
    case 'compass':
      if (p(v, 'COMPASS_USE') === 0) return 'skip'
      return ['X', 'Y', 'Z'].some((a) => isSet(p(v, `COMPASS_OFS_${a}`), 0)) && !v.prearm_messages.some((m) => /compass/i.test(m)) ? 'done' : 'todo'
    case 'rc':
      return rcCalibrated(v) ? 'done' : 'todo'
    case 'modes': {
      const modes = [1, 2, 3, 4, 5, 6].map((i) => p(v, `FLTMODE${i}`))
      return modes.includes(6) && modes.some((m) => m === 2 || m === 5 || m === 16) ? 'done' : 'todo'
    }
    case 'safety':
      return (p(v, 'FS_GCS_ENABLE') ?? 0) > 0 && (p(v, 'FS_THR_ENABLE') ?? 0) > 0 && (p(v, 'BATT_FS_LOW_ACT') ?? 0) > 0 && p(v, 'FENCE_ENABLE') === 1
        ? 'done' : 'todo'
    case 'motors':
      return 'optional'
    case 'camera':
      return v.camera_url ? 'done' : 'optional'
    case 'check':
      return v.prearm_messages.length === 0 && (v.heartbeat_age_s ?? 99) < 5 ? 'done' : 'todo'
    case 'register':
      return bound ? 'done' : 'todo'
  }
}

const OPTIONAL_STEPS: StepId[] = ['motors', 'camera']

/** Required steps only (the count stays the same while you work); skipped steps are not counted. */
export function progress(v: SetupView, bound: boolean): { done: number; required: number } {
  const st = STEPS.filter((s) => !OPTIONAL_STEPS.includes(s.id)).map((s) => stepStatus(s.id, v, bound)).filter((s) => s !== 'skip')
  return { done: st.filter((s) => s === 'done').length, required: st.length }
}

/** Plain-language hints for the ArduPilot pre-arm messages users meet most. */
export function explainPrearm(msg: string): string {
  const m = msg.replace(/^PreArm:\s*/i, '')
  const hints: [RegExp, string][] = [
    [/accel/i, 'Do step 4 (accelerometer calibration).'],
    [/compass|mag/i, 'Do step 5 (compass calibration), then restart.'],
    [/RC|radio|throttle/i, 'Check the transmitter is on, then do step 6 (RC calibration).'],
    [/frame/i, 'Do step 2 (frame type), then restart.'],
    [/GPS|home|fix/i, 'Go outdoors with a clear view of the sky and wait for the GPS.'],
    [/battery|voltage/i, 'Charge or replace the battery; check step 8.'],
    [/fence/i, 'Wait for a GPS fix: the fence needs a home position.'],
    [/gyro|INS|EKF|AHRS/i, 'Keep the drone still for a minute after power-on; restart if it persists.'],
  ]
  const hint = hints.find(([re]) => re.test(m))?.[1]
  return hint ? `${m}: ${hint}` : m
}
