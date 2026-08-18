import { Compass, Minus, Plus } from 'lucide-react'
import { useMap } from 'react-leaflet'

/** Spec 6.6: right rail docked on map views -- compass, zoom controls. Never relocates. */
export function InstrumentRail() {
  const map = useMap()

  return (
    <div
      style={{
        position: 'absolute',
        right: 'var(--dm-space-md)',
        top: 'var(--dm-space-md)',
        bottom: 'var(--dm-space-md)',
        zIndex: 20,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 'var(--dm-space-md)',
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          pointerEvents: 'auto',
          background: 'color-mix(in srgb, var(--dm-navy-900) 88%, transparent)',
          borderRadius: 'var(--dm-radius-circle)',
          width: '3.5rem',
          height: '3.5rem',
          display: 'grid',
          placeItems: 'center',
          boxShadow: '0 .4rem 1rem rgb(0 0 0 / .25)',
        }}
        aria-label="Compass"
      >
        <Compass size={26} color="var(--dm-text-on-dark)" />
      </div>

      <div
        style={{
          pointerEvents: 'auto',
          background: 'color-mix(in srgb, var(--dm-navy-900) 88%, transparent)',
          borderRadius: 'var(--dm-radius-md)',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 .4rem 1rem rgb(0 0 0 / .25)',
        }}
      >
        <button
          type="button"
          aria-label="Zoom in"
          onClick={() => map.zoomIn()}
          style={{ border: 0, background: 'transparent', color: 'var(--dm-text-on-dark)', width: '2.75rem', height: '2.75rem', display: 'grid', placeItems: 'center' }}
        >
          <Plus size={18} />
        </button>
        <button
          type="button"
          aria-label="Zoom out"
          onClick={() => map.zoomOut()}
          style={{ border: 0, background: 'transparent', color: 'var(--dm-text-on-dark)', width: '2.75rem', height: '2.75rem', display: 'grid', placeItems: 'center', borderTop: '1px solid rgb(255 255 255 / .1)' }}
        >
          <Minus size={18} />
        </button>
      </div>
    </div>
  )
}
