/** Metres <-> degrees for drawing ORCA vectors on the map (display only;
 * all collision maths happens on the backend in a local metric frame). */
const M_PER_DEG_LAT = 110_574

export function offsetLatLng(lat: number, lng: number, eastM: number, northM: number): [number, number] {
  const mPerDegLng = 111_320 * Math.cos((lat * Math.PI) / 180)
  return [lat + northM / M_PER_DEG_LAT, lng + eastM / mPerDegLng]
}

/** Polyline points for an arrow from (lat, lng) along velocity v (m/s) scaled by `seconds`. */
export function arrow(lat: number, lng: number, v: [number, number], seconds: number): [number, number][][] | null {
  const [vx, vy] = v
  const len = Math.hypot(vx, vy) * seconds
  if (len < 0.5) return null
  const tip = offsetLatLng(lat, lng, vx * seconds, vy * seconds)
  const head = Math.max(2.5, len * 0.22)
  const angle = Math.atan2(vy, vx)
  const wing = (delta: number) => offsetLatLng(tip[0], tip[1], -head * Math.cos(angle + delta), -head * Math.sin(angle + delta))
  return [[[lat, lng], tip], [wing(0.45), tip, wing(-0.45)]]
}

export function formatTtc(seconds: number): string {
  return `${seconds.toFixed(1)} s`
}
