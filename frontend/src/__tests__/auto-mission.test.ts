import { describe, expect, it } from 'vitest'
import { latLngRingToPolygon } from '../lib/mapGeometry'

describe('auto mission area', () => {
  it('turns map clicks ([lat, lng]) into a closed GeoJSON polygon ([lng, lat])', () => {
    const poly = latLngRingToPolygon([[23.24, 72.65], [23.24, 72.66], [23.25, 72.66], [23.25, 72.65]])
    expect(poly.type).toBe('Polygon')
    const ring = poly.coordinates[0]
    expect(ring[0]).toEqual([72.65, 23.24]) // longitude first: a swap would plan in the wrong place on Earth
    expect(ring).toHaveLength(5)
    expect(ring[ring.length - 1]).toEqual(ring[0])
  })
})
