/** Leaflet writes colours into SVG presentation attributes, where CSS
 * var() does not resolve, so map layers read token values at runtime. */
export function cssVar(name: string, fallback = '#888888'): string {
  if (typeof document === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

export const LANE_TOKENS = [
  '--dm-lane-1',
  '--dm-lane-2',
  '--dm-lane-3',
  '--dm-lane-4',
  '--dm-lane-5',
  '--dm-lane-6',
  '--dm-lane-7',
  '--dm-lane-8',
] as const

/** Deterministic lane colour token for a bundle's colour index (wraps after 8). */
export function laneToken(colorIndex: number): string {
  const n = LANE_TOKENS.length
  return LANE_TOKENS[((colorIndex % n) + n) % n]
}

export function laneColor(colorIndex: number): string {
  return cssVar(laneToken(colorIndex))
}

export const GEOFENCE_TOKENS = {
  inclusion: '--dm-geo-inclusion',
  exclusion: '--dm-geo-exclusion',
  nfz: '--dm-geo-nfz',
} as const
