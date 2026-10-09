export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '--'
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  return h > 0 ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}:${String(sec).padStart(2, '0')}`
}

export function formatDistance(meters: number | null | undefined): string {
  if (meters === null || meters === undefined || !Number.isFinite(meters)) return '--'
  return meters >= 1000 ? `${(meters / 1000).toFixed(2)} KM` : `${Math.round(meters)} M`
}

export function formatArea(sqMeters: number | null | undefined): string {
  if (!sqMeters) return '--'
  return sqMeters >= 1e6 ? `${(sqMeters / 1e6).toFixed(2)} KM²` : `${(sqMeters / 1e4).toFixed(2)} HA`
}

export function formatPct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--'
  return `${value.toFixed(digits)}%`
}

/** Closed GeoJSON ring (lng/lat) from Leaflet-style [lat, lng] vertices. */
export function closedRing(points: [number, number][]): number[][] {
  if (points.length === 0) return []
  const ring = points.map(([lat, lng]) => [lng, lat])
  return [...ring, ring[0]]
}

/** Axis-aligned rectangle (4 vertices, [lat, lng]) from two opposite corners. */
export function rectangleFromCorners(a: [number, number], b: [number, number]): [number, number][] {
  const [minLat, maxLat] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])]
  const [minLng, maxLng] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])]
  return [
    [minLat, minLng],
    [minLat, maxLng],
    [maxLat, maxLng],
    [maxLat, minLng],
  ]
}
