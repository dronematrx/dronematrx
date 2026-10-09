import type { ConstraintsConfig, Drone, FlightParams, SensorType } from '../api/types'

/** Client-side mirrors of the backend step rules: they block "Next" early
 * with clear messages. The backend re-validates everything regardless. */
export type FieldErrors = Record<string, string>

export const STEP_LABELS = ['Template', 'AOI', 'Flight', 'Sensors', 'Constraints', 'Validate'] as const

export function validateAoi(points: [number, number][]): FieldErrors {
  if (points.length < 3) return { aoi: 'Place at least 3 vertices (or use a rectangle / existing AOI)' }
  return {}
}

export function validateFlight(f: Partial<FlightParams>): FieldErrors {
  const e: FieldErrors = {}
  const range = (key: keyof FlightParams, min: number, max: number, label: string) => {
    const v = f[key]
    if (typeof v !== 'number' || !Number.isFinite(v) || v < min || v > max) e[key] = `${label} must be ${min}–${max}`
  }
  range('altitude_m', 10, 120, 'Altitude (m)')
  range('speed_mps', 1, 25, 'Speed (m/s)')
  range('overlap_pct', 0, 90, 'Overlap (%)')
  range('rth_battery_pct', 10, 60, 'RTH battery (%)')
  range('priority', 1, 5, 'Priority')
  if (f.heading_deg !== null && f.heading_deg !== undefined && (f.heading_deg < 0 || f.heading_deg >= 360)) {
    e.heading_deg = 'Heading must be 0–359°'
  }
  return e
}

export function droneCapabilities(drone: Drone): Set<string> {
  return new Set(drone.payload_summary.filter((p) => p.status !== 'fault').map((p) => p.type))
}

export function validateSensors(required: SensorType[], selected: Drone[], assignments: Record<string, SensorType | null>): FieldErrors {
  const e: FieldErrors = {}
  if (required.length === 0) e.required_sensors = 'Select at least one sensor'
  if (selected.length === 0) e.drone_ids = 'Select at least one drone'
  for (const sensor of required) {
    const carried = selected.some((d) => {
      const assigned = assignments[d.id]
      const caps = droneCapabilities(d)
      return assigned ? assigned === sensor && caps.has(sensor) : caps.has(sensor)
    })
    if (!carried && selected.length > 0) e[`sensor_${sensor}`] = `No selected drone carries a working ${sensor} payload`
  }
  return e
}

export function validateConstraints(c: Partial<ConstraintsConfig>, flight?: Partial<FlightParams>): FieldErrors {
  const e: FieldErrors = {}
  if (typeof c.min_battery_pct !== 'number' || c.min_battery_pct < 10 || c.min_battery_pct > 100) e.min_battery_pct = 'Minimum battery must be 10–100%'
  else if (flight?.rth_battery_pct !== undefined && c.min_battery_pct <= flight.rth_battery_pct) {
    e.min_battery_pct = `Must be above the RTH reserve (${flight.rth_battery_pct}%)`
  }
  if (c.max_flight_time_min !== null && c.max_flight_time_min !== undefined && (c.max_flight_time_min < 1 || c.max_flight_time_min > 180)) {
    e.max_flight_time_min = 'Max flight time must be 1–180 min'
  }
  if (c.max_range_km !== null && c.max_range_km !== undefined && (c.max_range_km < 0.1 || c.max_range_km > 100)) e.max_range_km = 'Max range must be 0.1–100 km'
  if (typeof c.max_tasks_per_drone !== 'number' || c.max_tasks_per_drone < 1 || c.max_tasks_per_drone > 60) e.max_tasks_per_drone = 'Tasks per drone must be 1–60'
  return e
}

export const hasErrors = (e: FieldErrors) => Object.keys(e).length > 0
