/** Approximate area (m²) of a [lat, lng] polygon: shoelace formula in a local
 * equirectangular frame at the polygon's mean latitude. Good to < 0.5 % for
 * mission-sized AOIs; the backend's PostGIS geography area is authoritative. */
export function polygonAreaM2(points: [number, number][]): number {
  if (points.length < 3) return 0
  const lat0 = points.reduce((s, p) => s + p[0], 0) / points.length
  const kx = 111_320 * Math.cos((lat0 * Math.PI) / 180)
  const ky = 110_574
  const xy = points.map(([lat, lng]) => [lng * kx, lat * ky])
  let a = 0
  for (let i = 0; i < xy.length; i++) {
    const [x0, y0] = xy[i]
    const [x1, y1] = xy[(i + 1) % xy.length]
    a += x0 * y1 - x1 * y0
  }
  return Math.abs(a) / 2
}
