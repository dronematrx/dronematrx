import { GEOFENCE_LABEL, ringToLatLng } from '../../lib/mapGeometry'
import { Polygon, Tooltip } from 'react-leaflet'
import type { Geofence, GeofenceType } from '../../api/types'
import { cssVar, GEOFENCE_TOKENS } from '../../lib/tokens'


interface GeofenceLayersProps {
  geofences: Geofence[]
  visible?: Partial<Record<GeofenceType, boolean>>
  highlightIds?: string[]
  onSelect?: (geofence: Geofence) => void
}

/** Inclusion = blue dashed outline, exclusion = red, NFZ = red hatched fill (PRD 5). */
export function GeofenceLayers({ geofences, visible = {}, highlightIds = [], onSelect }: GeofenceLayersProps) {
  return (
    <>
      {geofences
        .filter((g) => g.active && visible[g.type] !== false)
        .map((g) => {
          const color = cssVar(GEOFENCE_TOKENS[g.type])
          const highlighted = highlightIds.includes(g.id)
          const style =
            g.type === 'nfz'
              ? { color, weight: highlighted ? 4 : 2.5, fillOpacity: 1, className: 'dm-nfz-fill' }
              : g.type === 'exclusion'
                ? { color, fillColor: color, weight: highlighted ? 4 : 2, fillOpacity: 0.18 }
                : { color, fillColor: color, weight: highlighted ? 4 : 2, fillOpacity: 0.06, dashArray: '8 6' }
          return (
            <Polygon
              key={g.id}
              positions={ringToLatLng(g.geometry.coordinates[0])}
              pathOptions={style}
              eventHandlers={onSelect ? { click: () => onSelect(g) } : undefined}
            >
              <Tooltip sticky>
                {GEOFENCE_LABEL[g.type]}: {g.name}
                {g.altitude_max ? ` · ceiling ${g.altitude_max} m` : ''}
              </Tooltip>
            </Polygon>
          )
        })}
    </>
  )
}
