import { useEffect, useMemo, useRef, useState } from 'react'
import { Crosshair, Pencil, Square, Trash2 } from 'lucide-react'
import type {
  ConstraintsConfig,
  Drone,
  FlightParams,
  Geofence,
  Mission,
  MissionTemplate,
  MissionTemplateDef,
  SensorType,
  WizardState,
} from '../../api/types'
import { formatArea } from '../../lib/format'
import {
  droneCapabilities,
  hasErrors,
  validateAoi,
  validateConstraints,
  validateFlight,
  validateSensors,
  type FieldErrors,
} from '../../lib/wizardValidation'
import { StatusPill } from '../ui/StatusPill'
import type { DrawMode } from '../map/AoiDrawLayer'
import { FieldError, StepHeading, StepNav } from './WizardControls'

export type SaveIntent = 'next' | 'exit'

interface BaseStepProps {
  wizard: WizardState
  readOnly: boolean
  busy: boolean
  onSave: (data: unknown, intent: SaveIntent) => void
  onBack: () => void
}

// ------------------------------------------------------------------- Step 1

export function StepTemplate({ wizard, readOnly, busy, onSave, onBack, templates }: BaseStepProps & { templates: MissionTemplateDef[] }) {
  const [template, setTemplate] = useState<MissionTemplate | null>(wizard.template)
  const [name, setName] = useState(wizard.name)
  const [error, setError] = useState<string | null>(null)

  const submit = (intent: SaveIntent) => {
    if (!template) return setError('Choose a template to continue')
    if (!name.trim()) return setError('Mission name is required')
    onSave({ template, name: name.trim() }, intent)
  }

  return (
    <>
      <StepHeading title="Step 1 · Template">Pick a starting point. Templates pre-fill flight parameters and sensors.</StepHeading>
      <label className="dm-field">
        <span>Mission name</span>
        <input className="dm-input" value={name} disabled={readOnly} onChange={(e) => setName(e.target.value)} data-testid="mission-name" />
      </label>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 'var(--dm-space-sm)' }}>
        {templates.map((t) => (
          <button
            key={t.key}
            type="button"
            disabled={readOnly}
            onClick={() => {
              setTemplate(t.key)
              setError(null)
            }}
            aria-pressed={template === t.key}
            data-testid={`template-${t.key}`}
            className="dm-card"
            style={{
              textAlign: 'left',
              border: template === t.key ? '2px solid var(--dm-text-on-dark)' : '2px solid transparent',
              color: 'var(--dm-text-on-dark)',
              display: 'grid',
              gap: '.35rem',
            }}
          >
            <strong style={{ textTransform: 'uppercase', fontSize: '.85rem' }}>{t.label}</strong>
            <span style={{ fontSize: '.75rem', opacity: 0.8 }}>{t.description}</span>
            <span className="dm-mono" style={{ fontSize: '.7rem', opacity: 0.7 }}>
              {t.flight.altitude_m}M · {t.flight.overlap_pct}% · {t.required_sensors.join('+').toUpperCase()}
            </span>
          </button>
        ))}
      </div>
      {error && <FieldError message={error} />}
      <StepNav step={1} canGoBack={false} onBack={onBack} onSaveExit={() => submit('exit')} onNext={() => submit('next')} busy={busy} readOnly={readOnly} />
    </>
  )
}

// ------------------------------------------------------------------- Step 2

export interface AoiDrawControls {
  mode: DrawMode
  setMode: (mode: DrawMode) => void
  drawing: boolean
  setDrawing: (v: boolean) => void
  points: [number, number][]
  setPoints: (p: [number, number][]) => void
}

export function StepAoi({ wizard, readOnly, busy, onSave, onBack, draw, missions }: BaseStepProps & { draw: AoiDrawControls; missions: Mission[] }) {
  const [copyFrom, setCopyFrom] = useState('')
  const [error, setError] = useState<string | null>(null)
  const sources = missions.filter((m) => m.id !== wizard.mission_id && m.aoi_geometry)

  const submit = (intent: SaveIntent) => {
    setError(null)
    if (draw.points.length > 0) {
      const errs = validateAoi(draw.points)
      if (hasErrors(errs)) return setError(errs.aoi)
      const ring = draw.points.map(([lat, lng]) => [lng, lat])
      return onSave({ source: draw.mode === 'rectangle' ? 'rectangle' : 'drawn', geometry: { type: 'Polygon', coordinates: [[...ring, ring[0]]] } }, intent)
    }
    if (copyFrom) return onSave({ source: 'copied', copy_from_mission_id: copyFrom }, intent)
    if (wizard.aoi_geometry) return onSave({ source: 'existing' }, intent)
    setError('Draw a polygon or rectangle on the map, or pick an existing AOI')
  }

  const start = (mode: DrawMode) => {
    draw.setMode(mode)
    draw.setPoints([])
    draw.setDrawing(true)
    setCopyFrom('')
  }

  return (
    <>
      <StepHeading title="Step 2 · Area of Interest">Draw the survey area on the map. NFZs (hatched red) and geofences are shown for reference.</StepHeading>
      {wizard.aoi_geometry && (
        <div className="dm-card">
          <div className="dm-card-title">Saved AOI&hellip;</div>
          <div className="dm-kv">
            <span>Area</span>
            <span className="dm-mono">{formatArea(wizard.aoi_area_sq_meters)}</span>
          </div>
          <div className="dm-kv">
            <span>Vertices</span>
            <span className="dm-mono">{wizard.aoi_geometry.coordinates[0].length - 1}</span>
          </div>
        </div>
      )}
      {!readOnly && (
        <div className="dm-card" style={{ display: 'grid', gap: 'var(--dm-space-sm)' }}>
          <div className="dm-card-title">Draw&hellip;</div>
          <div style={{ display: 'flex', gap: 'var(--dm-space-sm)' }}>
            <button type="button" className={`dm-btn ${draw.drawing && draw.mode === 'polygon' ? 'dm-btn--primary' : ''}`} onClick={() => start('polygon')} data-testid="draw-polygon">
              <Pencil size={14} /> Polygon
            </button>
            <button type="button" className={`dm-btn ${draw.drawing && draw.mode === 'rectangle' ? 'dm-btn--primary' : ''}`} onClick={() => start('rectangle')} data-testid="draw-rectangle">
              <Square size={14} /> Rectangle
            </button>
            <button type="button" className="dm-btn dm-btn--ghost" disabled={draw.points.length === 0} onClick={() => draw.setPoints([])}>
              <Trash2 size={14} /> Clear
            </button>
          </div>
          <p className="dm-mono" style={{ fontSize: '.7rem', opacity: 0.75, margin: 0 }} data-testid="aoi-vertex-count">
            {draw.drawing
              ? draw.mode === 'rectangle'
                ? draw.points.length === 4
                  ? 'RECTANGLE READY · 4 VERTICES'
                  : 'CLICK TWO OPPOSITE CORNERS'
                : `CLICK TO ADD VERTICES · ${draw.points.length} PLACED`
              : `${draw.points.length} DRAFT VERTICES`}
          </p>
          {sources.length > 0 && (
            <label className="dm-field">
              <span>Or copy an existing AOI</span>
              <select
                className="dm-input"
                value={copyFrom}
                onChange={(e) => {
                  setCopyFrom(e.target.value)
                  draw.setPoints([])
                  draw.setDrawing(false)
                }}
              >
                <option value="">—</option>
                {sources.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name} ({formatArea(m.aoi_area_sq_meters)})
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      )}
      {error && <FieldError message={error} />}
      <StepNav step={2} canGoBack onBack={onBack} onSaveExit={() => submit('exit')} onNext={() => submit('next')} busy={busy} readOnly={readOnly} />
    </>
  )
}

// ------------------------------------------------------------------- Step 3

const FLIGHT_DEFAULTS: FlightParams = {
  altitude_m: 60,
  altitude_ref: 'AGL',
  speed_mps: 10,
  overlap_pct: 70,
  pattern: 'lawnmower',
  heading_deg: null,
  rth_on_complete: true,
  rth_battery_pct: 25,
  priority: 2,
}

function SliderField({ label, unit, value, min, max, step = 1, disabled, onChange, error, testId }: {
  label: string
  unit: string
  value: number
  min: number
  max: number
  step?: number
  disabled?: boolean
  onChange: (v: number) => void
  error?: string
  testId?: string
}) {
  return (
    <label className="dm-field">
      <span>
        {label} ({unit})
      </span>
      <div style={{ display: 'flex', gap: 'var(--dm-space-sm)', alignItems: 'center' }}>
        <input type="range" min={min} max={max} step={step} value={value} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))} style={{ flex: 1 }} aria-label={`${label} slider`} />
        <input
          type="number"
          className="dm-input dm-mono"
          style={{ width: '5.5rem' }}
          min={min}
          max={max}
          step={step}
          value={Number.isFinite(value) ? value : ''}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value === '' ? NaN : Number(e.target.value))}
          data-testid={testId}
        />
      </div>
      <FieldError message={error} />
    </label>
  )
}

export function StepFlight({ wizard, readOnly, busy, onSave, onBack, onAutoSave }: BaseStepProps & { onAutoSave: (data: FlightParams) => void }) {
  const [flight, setFlight] = useState<FlightParams>({ ...FLIGHT_DEFAULTS, ...(wizard.config.flight ?? {}) })
  const [errors, setErrors] = useState<FieldErrors>({})
  const firstRender = useRef(true)
  const canAutoSave = !readOnly && wizard.plan_id !== null && wizard.completed_steps.includes(3)

  // Auto-save (debounced) once a plan exists, so ETA/duration update live.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false
      return
    }
    if (!canAutoSave || hasErrors(validateFlight(flight))) return
    const timer = window.setTimeout(() => onAutoSave(flight), 700)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- debounce on value changes only
  }, [flight])

  const set = <K extends keyof FlightParams>(key: K, value: FlightParams[K]) => setFlight((prev) => ({ ...prev, [key]: value }))

  const submit = (intent: SaveIntent) => {
    const errs = validateFlight(flight)
    setErrors(errs)
    if (!hasErrors(errs)) onSave(flight, intent)
  }

  return (
    <>
      <StepHeading title="Step 3 · Flight Parameters">
        {canAutoSave ? 'Changes re-plan automatically; ETAs update on the map.' : 'Altitude and overlap set lane spacing; speed sets ETA.'}
      </StepHeading>
      <div style={{ display: 'flex', gap: 'var(--dm-space-sm)' }}>
        <div style={{ flex: 1 }}>
          <SliderField label="Altitude" unit="m" value={flight.altitude_m} min={10} max={120} disabled={readOnly} onChange={(v) => set('altitude_m', v)} error={errors.altitude_m} testId="flight-altitude" />
        </div>
        <label className="dm-field" style={{ width: '5.5rem' }}>
          <span>Ref</span>
          <select className="dm-input" value={flight.altitude_ref} disabled={readOnly} onChange={(e) => set('altitude_ref', e.target.value as 'AGL' | 'MSL')}>
            <option>AGL</option>
            <option>MSL</option>
          </select>
        </label>
      </div>
      <SliderField label="Speed" unit="m/s" value={flight.speed_mps} min={1} max={25} step={0.5} disabled={readOnly} onChange={(v) => set('speed_mps', v)} error={errors.speed_mps} testId="flight-speed" />
      <SliderField label="Side overlap" unit="%" value={flight.overlap_pct} min={0} max={90} disabled={readOnly} onChange={(v) => set('overlap_pct', v)} error={errors.overlap_pct} testId="flight-overlap" />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--dm-space-sm)' }}>
        <label className="dm-field">
          <span>Flight pattern</span>
          <select className="dm-input" value={flight.pattern} disabled={readOnly} onChange={(e) => set('pattern', e.target.value as FlightParams['pattern'])} data-testid="flight-pattern">
            <option value="lawnmower">Lawnmower</option>
            <option value="crosshatch">Crosshatch</option>
          </select>
        </label>
        <label className="dm-field">
          <span>Heading lock (°)</span>
          <input
            className="dm-input dm-mono"
            type="number"
            min={0}
            max={359}
            placeholder="Auto"
            value={flight.heading_deg ?? ''}
            disabled={readOnly}
            onChange={(e) => set('heading_deg', e.target.value === '' ? null : Number(e.target.value))}
          />
          <FieldError message={errors.heading_deg} />
        </label>
      </div>
      <SliderField label="RTH battery reserve" unit="%" value={flight.rth_battery_pct} min={10} max={60} disabled={readOnly} onChange={(v) => set('rth_battery_pct', v)} error={errors.rth_battery_pct} testId="flight-rth" />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--dm-space-sm)', alignItems: 'end' }}>
        <label className="dm-field">
          <span>Mission priority</span>
          <select className="dm-input" value={flight.priority} disabled={readOnly} onChange={(e) => set('priority', Number(e.target.value))}>
            {[1, 2, 3, 4, 5].map((p) => (
              <option key={p} value={p}>
                P{p}
                {p === 5 ? ' (critical)' : p === 1 ? ' (routine)' : ''}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: 'flex', gap: '.5rem', alignItems: 'center', fontSize: '.85rem', paddingBottom: '.5rem' }}>
          <input type="checkbox" checked={flight.rth_on_complete} disabled={readOnly} onChange={(e) => set('rth_on_complete', e.target.checked)} />
          RTH on completion
        </label>
      </div>
      <StepNav step={3} canGoBack onBack={onBack} onSaveExit={() => submit('exit')} onNext={() => submit('next')} busy={busy} readOnly={readOnly} />
    </>
  )
}

// ------------------------------------------------------------------- Step 4

const SENSORS: SensorType[] = ['camera', 'thermal', 'multispectral', 'lidar', 'sprayer']

export function StepSensors({ wizard, readOnly, busy, onSave, onBack, drones }: BaseStepProps & { drones: Drone[] }) {
  const cfg = wizard.config.sensors ?? {}
  const [required, setRequired] = useState<SensorType[]>(cfg.required_sensors?.length ? cfg.required_sensors : ['camera'])
  const usable = drones.filter((d) => d.active)
  const [selectedIds, setSelectedIds] = useState<string[]>(
    cfg.drone_ids?.length ? cfg.drone_ids : usable.filter((d) => !['offline', 'maintenance'].includes(d.status)).map((d) => d.id),
  )
  const [assignments, setAssignments] = useState<Record<string, SensorType | null>>(cfg.payload_assignments ?? {})
  const [errors, setErrors] = useState<FieldErrors>({})

  const selected = useMemo(() => usable.filter((d) => selectedIds.includes(d.id)), [usable, selectedIds])
  const liveErrors = validateSensors(required, selected, assignments)

  const toggleSensor = (s: SensorType) => setRequired((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]))
  const toggleDrone = (id: string) => setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))

  const submit = (intent: SaveIntent) => {
    setErrors(liveErrors)
    if (hasErrors(liveErrors)) return
    const payload_assignments = Object.fromEntries(Object.entries(assignments).filter(([id, v]) => selectedIds.includes(id) && v))
    onSave({ required_sensors: required, drone_ids: selectedIds, payload_assignments }, intent)
  }

  return (
    <>
      <StepHeading title="Step 4 · Sensors & Payloads">A drone can only be assigned tasks for sensors it actually carries (faulted payloads excluded).</StepHeading>
      <div className="dm-field">
        <span>Required sensors</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--dm-space-xs)' }}>
          {SENSORS.map((s) => (
            <button
              key={s}
              type="button"
              disabled={readOnly}
              aria-pressed={required.includes(s)}
              className={`dm-btn ${required.includes(s) ? 'dm-btn--primary' : 'dm-btn--ghost'}`}
              onClick={() => toggleSensor(s)}
              data-testid={`sensor-${s}`}
            >
              {s}
            </button>
          ))}
        </div>
        <FieldError message={errors.required_sensors} />
        {required.map((s) => (
          <FieldError key={s} message={liveErrors[`sensor_${s}`]} />
        ))}
      </div>
      <div className="dm-field">
        <span>
          Drones ({selectedIds.length} selected)
        </span>
        <div style={{ display: 'grid', gap: '.35rem' }}>
          {usable.map((d) => {
            const caps = droneCapabilities(d)
            const compatible = required.some((s) => caps.has(s))
            const unavailable = ['offline', 'maintenance', 'critical'].includes(d.status)
            return (
              <div key={d.id} className="dm-card" style={{ padding: '.5rem .75rem', display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: '.5rem', alignItems: 'center', opacity: unavailable ? 0.6 : 1 }}>
                <input type="checkbox" checked={selectedIds.includes(d.id)} disabled={readOnly} onChange={() => toggleDrone(d.id)} aria-label={`Select ${d.name}`} data-testid={`drone-select-${d.name}`} />
                <div>
                  <div style={{ fontWeight: 600, fontSize: '.85rem' }}>
                    {d.name} <span className="dm-mono" style={{ opacity: 0.7, fontWeight: 400 }}>{d.battery.toFixed(0)}%</span>
                  </div>
                  <div style={{ display: 'flex', gap: '.25rem', flexWrap: 'wrap', marginTop: '.2rem' }}>
                    {d.payload_summary.length === 0 && <span style={{ fontSize: '.7rem', opacity: 0.6 }}>no payloads</span>}
                    {d.payload_summary.map((p) => (
                      <span key={p.name} title={`${p.name}: ${p.status}`} style={{ fontSize: '.65rem', textTransform: 'uppercase', padding: '.1rem .4rem', borderRadius: 'var(--dm-radius-pill)', background: p.status === 'calibrated' ? 'var(--dm-status-active)' : p.status === 'fault' ? 'var(--dm-status-alert)' : 'var(--dm-status-armed)' }}>
                        {p.type} · {p.status}
                      </span>
                    ))}
                  </div>
                  {selectedIds.includes(d.id) && !compatible && <span className="dm-error">Carries none of the required sensors</span>}
                  {unavailable && <span className="dm-error">Status {d.status}: will be excluded</span>}
                </div>
                {selectedIds.includes(d.id) && caps.size > 1 && (
                  <select
                    className="dm-input"
                    aria-label={`${d.name} payload`}
                    value={assignments[d.id] ?? ''}
                    disabled={readOnly}
                    onChange={(e) => setAssignments((prev) => ({ ...prev, [d.id]: (e.target.value || null) as SensorType | null }))}
                  >
                    <option value="">All payloads</option>
                    {[...caps].map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            )
          })}
        </div>
        <FieldError message={errors.drone_ids} />
      </div>
      <StepNav step={4} canGoBack onBack={onBack} onSaveExit={() => submit('exit')} onNext={() => submit('next')} busy={busy} readOnly={readOnly} />
    </>
  )
}

// ------------------------------------------------------------------- Step 5

const CONSTRAINT_DEFAULTS: ConstraintsConfig = {
  min_battery_pct: 40,
  max_flight_time_min: null,
  max_range_km: null,
  max_tasks_per_drone: 15,
  geofence_ids: [],
  link_loss_policy: 'RTH',
  accept_unassigned: false,
  inspection_points: [],
  algorithm: 'cbba',
}

export interface PointPicker {
  picking: boolean
  setPicking: (v: boolean) => void
  /** Step 5 registers what a map click should do while picking. */
  setHandler: (handler: ((point: [number, number]) => void) | null) => void
}

export function StepConstraints({ wizard, readOnly, busy, onSave, onBack, geofences, picker }: BaseStepProps & { geofences: Geofence[]; picker: PointPicker }) {
  const initial = { ...CONSTRAINT_DEFAULTS, ...(wizard.config.constraints ?? {}) }
  const [c, setC] = useState<ConstraintsConfig>(
    // Safety default: enforce every exclusion zone; inclusion zones are opt-in.
    wizard.config.constraints ? initial : { ...initial, geofence_ids: geofences.filter((g) => g.active && g.type === 'exclusion').map((g) => g.id) },
  )
  const [errors, setErrors] = useState<FieldErrors>({})
  const [pointSensor, setPointSensor] = useState<SensorType | ''>('')
  const set = <K extends keyof ConstraintsConfig>(key: K, value: ConstraintsConfig[K]) => setC((prev) => ({ ...prev, [key]: value }))

  // Map clicks in pick mode become inspection-point tasks.
  const { setHandler } = picker
  useEffect(() => {
    setHandler(([lat, lng]) =>
      setC((prev) => ({
        ...prev,
        inspection_points: [
          ...prev.inspection_points,
          { latitude: lat, longitude: lng, required_sensor: pointSensor || null, priority: 3, hover_time_s: 60 },
        ].slice(0, 20),
      })),
    )
    return () => setHandler(null)
  }, [setHandler, pointSensor])

  const zones = geofences.filter((g) => g.active && g.type !== 'nfz')
  const nfzs = geofences.filter((g) => g.active && g.type === 'nfz')

  const submit = (intent: SaveIntent) => {
    const errs = validateConstraints(c, wizard.config.flight)
    setErrors(errs)
    if (hasErrors(errs)) return
    picker.setPicking(false)
    onSave(c, intent)
  }

  const numberOrNull = (v: string) => (v === '' ? null : Number(v))

  return (
    <>
      <StepHeading title="Step 5 · Constraints">Hard limits the allocator must respect. NFZs are always enforced.</StepHeading>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--dm-space-sm)' }}>
        <label className="dm-field">
          <span>Min launch battery %</span>
          <input className="dm-input dm-mono" type="number" value={c.min_battery_pct} disabled={readOnly} onChange={(e) => set('min_battery_pct', Number(e.target.value))} data-testid="constraint-min-battery" />
          <FieldError message={errors.min_battery_pct} />
        </label>
        <label className="dm-field">
          <span>Max tasks / drone</span>
          <input className="dm-input dm-mono" type="number" value={c.max_tasks_per_drone} disabled={readOnly} onChange={(e) => set('max_tasks_per_drone', Number(e.target.value))} data-testid="constraint-max-tasks" />
          <FieldError message={errors.max_tasks_per_drone} />
        </label>
        <label className="dm-field">
          <span>Max flight time (min)</span>
          <input className="dm-input dm-mono" type="number" placeholder="Battery-limited" value={c.max_flight_time_min ?? ''} disabled={readOnly} onChange={(e) => set('max_flight_time_min', numberOrNull(e.target.value))} />
          <FieldError message={errors.max_flight_time_min} />
        </label>
        <label className="dm-field">
          <span>Max range (km)</span>
          <input className="dm-input dm-mono" type="number" step="0.1" placeholder="Unlimited" value={c.max_range_km ?? ''} disabled={readOnly} onChange={(e) => set('max_range_km', numberOrNull(e.target.value))} />
          <FieldError message={errors.max_range_km} />
        </label>
      </div>
      <label className="dm-field">
        <span>Link-loss policy</span>
        <select className="dm-input" value={c.link_loss_policy} disabled={readOnly} onChange={(e) => set('link_loss_policy', e.target.value as ConstraintsConfig['link_loss_policy'])}>
          <option value="RTH">Return to home</option>
          <option value="HOLD">Hold position</option>
          <option value="CONTINUE">Continue waypoints</option>
          <option value="LAND">Land at nearest safe site</option>
        </select>
      </label>
      <div className="dm-field">
        <span>Enforced geofences</span>
        {zones.length === 0 && <span style={{ fontSize: '.75rem', opacity: 0.6 }}>No inclusion/exclusion zones in the library.</span>}
        {zones.map((g) => (
          <label key={g.id} style={{ display: 'flex', gap: '.5rem', alignItems: 'center', fontSize: '.8rem' }}>
            <input
              type="checkbox"
              checked={c.geofence_ids.includes(g.id)}
              disabled={readOnly}
              onChange={(e) => set('geofence_ids', e.target.checked ? [...c.geofence_ids, g.id] : c.geofence_ids.filter((id) => id !== g.id))}
            />
            <StatusPill label={g.type} /> {g.name}
          </label>
        ))}
        {nfzs.map((g) => (
          <label key={g.id} style={{ display: 'flex', gap: '.5rem', alignItems: 'center', fontSize: '.8rem', opacity: 0.85 }}>
            <input type="checkbox" checked disabled aria-label={`${g.name} always enforced`} />
            <StatusPill label="nfz" /> {g.name} <em style={{ opacity: 0.7 }}>(always enforced)</em>
          </label>
        ))}
      </div>
      <div className="dm-field">
        <span>Inspection points ({c.inspection_points.length}/20)</span>
        {!readOnly && (
          <div style={{ display: 'flex', gap: 'var(--dm-space-xs)' }}>
            <select className="dm-input" value={pointSensor} onChange={(e) => setPointSensor(e.target.value as SensorType | '')} aria-label="Inspection sensor">
              <option value="">Any sensor</option>
              {SENSORS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <button type="button" className={`dm-btn ${picker.picking ? 'dm-btn--primary' : ''}`} onClick={() => picker.setPicking(!picker.picking)} data-testid="pick-inspection-point">
              <Crosshair size={14} /> {picker.picking ? 'Click map…' : 'Add on map'}
            </button>
          </div>
        )}
        {c.inspection_points.map((p, i) => (
          <div key={i} className="dm-kv">
            <span className="dm-mono">
              P{String(i + 1).padStart(3, '0')} {p.latitude.toFixed(5)}, {p.longitude.toFixed(5)} · {p.required_sensor ?? 'any'}
            </span>
            {!readOnly && (
              <button type="button" className="dm-btn dm-btn--ghost" style={{ padding: '.1rem .4rem' }} aria-label="Remove inspection point" onClick={() => set('inspection_points', c.inspection_points.filter((_, k) => k !== i))}>
                <Trash2 size={12} />
              </button>
            )}
          </div>
        ))}
      </div>
      <label style={{ display: 'flex', gap: '.5rem', alignItems: 'center', fontSize: '.85rem' }}>
        <input type="checkbox" checked={c.accept_unassigned} disabled={readOnly} onChange={(e) => set('accept_unassigned', e.target.checked)} data-testid="constraint-accept-unassigned" />
        Accept unassigned tasks (launch without full coverage)
      </label>
      <StepNav step={5} canGoBack onBack={onBack} onSaveExit={() => submit('exit')} onNext={() => submit('next')} nextLabel="Validate" busy={busy} readOnly={readOnly} />
    </>
  )
}
