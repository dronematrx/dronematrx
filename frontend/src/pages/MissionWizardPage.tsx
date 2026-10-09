import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import type { LatLngTuple } from 'leaflet'
import { CircleMarker, Polygon, Tooltip, useMapEvents } from 'react-leaflet'
import { X } from 'lucide-react'
import * as geofencesApi from '../api/geofences'
import type { FlightParams, Geofence } from '../api/types'
import { AppShell } from '../components/layout/AppShell'
import { AoiDrawLayer, type DrawMode } from '../components/map/AoiDrawLayer'
import { CbbaLanes, ConflictOverlay } from '../components/map/CbbaLanes'
import { GeofenceLayers } from '../components/map/GeofenceLayers'
import { MissionMap } from '../components/map/MissionMap'
import { polygonBounds, ringToLatLng } from '../lib/mapGeometry'
import { MissionSummaryPanel } from '../components/mission/MissionPanels'
import { StepValidate } from '../components/mission/StepValidate'
import { WizardStepper } from '../components/mission/WizardControls'
import { StepAoi, StepConstraints, StepFlight, StepSensors, StepTemplate, type SaveIntent } from '../components/mission/WizardSteps'
import { StatusPill } from '../components/ui/StatusPill'
import { formatDuration, formatPct } from '../lib/format'
import { cssVar } from '../lib/tokens'
import { useAuthStore } from '../store/useAuthStore'
import { useDroneStore } from '../store/useDroneStore'
import { useMissionStore } from '../store/useMissionStore'

/** Map clicks while picking inspection points (wizard step 5). */
function PickLayer({ active, onPick }: { active: boolean; onPick: (p: [number, number]) => void }) {
  useMapEvents({
    click(e) {
      if (active) onPick([e.latlng.lat, e.latlng.lng])
    },
  })
  return null
}

export function MissionWizardPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const role = useAuthStore((s) => s.user?.role)
  const canWrite = role === 'ADMIN' || role === 'OPERATOR'

  const drones = useDroneStore((s) => s.drones)
  const fetchDrones = useDroneStore((s) => s.fetchDrones)
  const connectTelemetry = useDroneStore((s) => s.connectTelemetry)

  const store = useMissionStore()
  const { wizard, plan, validation, readiness, busy, error, templates, missions, realtimeConnected } = store
  const { openMission, closeMission, fetchTemplates, fetchMissions, saveStep, clearError } = store

  const [geofences, setGeofences] = useState<Geofence[]>([])
  const [stepOverride, setStepOverride] = useState<number | null>(null)
  const [selectedDroneId, setSelectedDroneId] = useState<string | null>(null)

  const [drawMode, setDrawMode] = useState<DrawMode>('polygon')
  const [drawing, setDrawing] = useState(false)
  const [draftPoints, setDraftPoints] = useState<[number, number][]>([])

  const [picking, setPicking] = useState(false)
  const pickHandler = useRef<((p: [number, number]) => void) | null>(null)
  const setPickHandler = useCallback((fn: ((p: [number, number]) => void) | null) => {
    pickHandler.current = fn
  }, [])

  useEffect(() => {
    openMission(id)
    fetchTemplates()
    fetchMissions()
    fetchDrones()
    connectTelemetry()
    geofencesApi.listGeofences().then(setGeofences).catch(() => setGeofences([]))
    return () => closeMission()
  }, [id, openMission, closeMission, fetchTemplates, fetchMissions, fetchDrones, connectTelemetry])

  const step = stepOverride ?? Math.min(wizard?.wizard_step ?? 1, 6)
  const furthest = Math.min(Math.max(wizard?.wizard_step ?? 1, step), 6)
  const readOnly = !canWrite || !(wizard?.editable ?? false)

  const goTo = (target: number) => {
    setStepOverride(target)
    clearError()
    // The store may already show `target` (it publishes wizard_step on save);
    // only reset map interaction when the visible step really changes, so a
    // drawing the operator just started is not cancelled.
    if (target !== step) {
      setDrawing(false)
      setPicking(false)
    }
  }

  const handleSave = async (savedStep: number, data: unknown, intent: SaveIntent) => {
    const result = await saveStep(savedStep, data)
    if (!result) return // error is shown; stay on the step
    if (savedStep === 2) {
      setDraftPoints([])
      setDrawing(false)
    }
    if (intent === 'exit') {
      navigate('/missions')
      return
    }
    goTo(savedStep + 1)
    if (savedStep === 5) await useMissionStore.getState().validate() // step 6 opens with CBBA + checks run
  }

  const autoSaveFlight = (flight: FlightParams) => {
    saveStep(3, flight)
  }

  const aoiRing = wizard?.aoi_geometry?.coordinates[0] ?? null
  const fitTo = useMemo(() => polygonBounds(aoiRing), [aoiRing])
  const fitKey = aoiRing ? `${id}:${aoiRing[0]?.join(',')}:${aoiRing.length}` : null
  const inspection = wizard?.config.constraints?.inspection_points ?? []

  if (!wizard) {
    return (
      <AppShell title="Mission Planning">
        <div className="dm-card" style={{ background: 'var(--dm-navy-900)' }}>
          {error ? (
            <>
              <p className="dm-error">{error.message}</p>
              <button type="button" className="dm-btn" onClick={() => navigate('/missions')}>
                Back to missions
              </button>
            </>
          ) : (
            'Loading mission…'
          )}
        </div>
      </AppShell>
    )
  }

  const stepProps = { wizard, readOnly, busy: busy === 'saving', onBack: () => goTo(Math.max(1, step - 1)) }

  return (
    <AppShell
      title={wizard.name}
      fullBleed
      actions={
        <>
          <StatusPill label={wizard.status} />
          <button type="button" className="dm-btn dm-btn--ghost" onClick={() => navigate('/missions')} data-testid="wizard-cancel">
            <X size={14} /> {readOnly ? 'Close' : 'Cancel'}
          </button>
        </>
      }
    >
      <WizardStepper current={step} completed={wizard.completed_steps} furthest={furthest} onSelect={goTo} />
      <div className="dm-wizard-grid" style={{ flex: 1, minHeight: 0 }}>
        <section className="dm-wizard-panel" aria-label={`Wizard step ${step}`} data-testid={`wizard-step-${step}`}>
          {error && (
            <div className="dm-banner dm-banner--bad" role="alert" style={{ fontWeight: 400, textTransform: 'none', letterSpacing: 0, fontSize: '.8rem' }} data-testid="wizard-error">
              <span style={{ flex: 1 }}>
                {error.code ? <strong className="dm-mono">{error.code}: </strong> : null}
                {error.message}
              </span>
              <button type="button" aria-label="Dismiss error" onClick={clearError} style={{ background: 'transparent', border: 0, color: 'inherit' }}>
                <X size={14} />
              </button>
            </div>
          )}
          {readOnly && step < 6 && (
            <p style={{ fontSize: '.75rem', opacity: 0.75, margin: 0 }}>{canWrite ? `Plan locked: mission is ${wizard.status.toUpperCase()}.` : 'Read-only view (your role cannot edit missions).'}</p>
          )}
          {step === 1 && <StepTemplate key={`${id}-1`} {...stepProps} templates={templates} onSave={(d, i) => handleSave(1, d, i)} />}
          {step === 2 && (
            <StepAoi
              key={`${id}-2`}
              {...stepProps}
              missions={missions}
              draw={{ mode: drawMode, setMode: setDrawMode, drawing, setDrawing, points: draftPoints, setPoints: setDraftPoints }}
              onSave={(d, i) => handleSave(2, d, i)}
            />
          )}
          {step === 3 && <StepFlight key={`${id}-3`} {...stepProps} onAutoSave={autoSaveFlight} onSave={(d, i) => handleSave(3, d, i)} />}
          {step === 4 && <StepSensors key={`${id}-4`} {...stepProps} drones={drones} onSave={(d, i) => handleSave(4, d, i)} />}
          {step === 5 && (
            <StepConstraints
              key={`${id}-5`}
              {...stepProps}
              geofences={geofences}
              picker={{ picking, setPicking, setHandler: setPickHandler }}
              onSave={(d, i) => handleSave(5, d, i)}
            />
          )}
          {step === 6 && (
            <StepValidate
              wizard={wizard}
              plan={plan}
              validation={validation}
              readiness={readiness}
              error={error}
              busy={busy}
              readOnly={!canWrite}
              drones={drones}
              selectedDroneId={selectedDroneId}
              onSelectLane={setSelectedDroneId}
              onBack={() => goTo(5)}
              onRunCbba={store.runAllocation}
              onValidate={store.validate}
              onTransition={(target, reason) => store.transition(target, reason)}
              onAddDrone={store.addDrone}
              onRemoveDrone={(droneId) => {
                setSelectedDroneId(null)
                store.removeDrone(droneId)
              }}
              onLoadReadiness={store.loadReadiness}
            />
          )}
        </section>

        <div className="dm-wizard-map" style={{ position: 'relative', minHeight: 0 }} data-testid="wizard-map">
          <MissionMap drones={drones} fitTo={fitTo} fitKey={fitKey}>
            <GeofenceLayers geofences={geofences} />
            {aoiRing && !(drawing && draftPoints.length > 0) && (
              <Polygon positions={ringToLatLng(aoiRing)} pathOptions={{ color: cssVar('--dm-aoi-stroke'), fillColor: cssVar('--dm-navy-900'), fillOpacity: 0.2, weight: 2, dashArray: plan ? '4 4' : undefined }} />
            )}
            {plan && <CbbaLanes bundles={plan.bundles} tasks={plan.tasks} selectedDroneId={selectedDroneId} onSelect={(b) => setSelectedDroneId(b.drone_id)} dimmed={plan.stale} />}
            {validation && <ConflictOverlay conflicts={validation.conflicts} />}
            {inspection.map((p, i) => (
              <CircleMarker key={i} center={[p.latitude, p.longitude] as LatLngTuple} radius={6} pathOptions={{ color: '#FFFFFF', fillColor: cssVar('--dm-status-armed'), fillOpacity: 1, weight: 2 }}>
                <Tooltip>
                  P{String(i + 1).padStart(3, '0')} · {p.required_sensor ?? 'any sensor'}
                </Tooltip>
              </CircleMarker>
            ))}
            <AoiDrawLayer
              active={step === 2 && drawing && !readOnly}
              mode={drawMode}
              points={draftPoints as LatLngTuple[]}
              onAddPoint={(p) => setDraftPoints((prev) => [...prev, p as [number, number]])}
              onReplacePoints={(pts) => setDraftPoints(pts as [number, number][])}
            />
            <PickLayer active={step === 5 && picking} onPick={(p) => pickHandler.current?.(p)} />
          </MissionMap>
          <div
            style={{ position: 'absolute', left: 0, right: 0, bottom: 0, zIndex: 1000, background: 'color-mix(in srgb, var(--dm-slate-500) 92%, transparent)', padding: '.45rem var(--dm-space-md)', display: 'flex', gap: 'var(--dm-space-md)', fontSize: '.75rem', flexWrap: 'wrap' }}
            className="dm-mono"
          >
            <span>STEP {step}/6</span>
            <span>LANES {plan?.bundles.length ?? 0}</span>
            <span>ETA {plan ? formatDuration(plan.estimated_duration_s) : '--'}</span>
            <span>COV {plan ? formatPct(plan.coverage_pct) : '--'}</span>
            {(drawing || picking) && <span style={{ color: '#FFD08A' }}>{picking ? 'CLICK MAP: INSPECTION POINT' : 'CLICK MAP: DRAW AOI'}</span>}
            <span style={{ marginLeft: 'auto' }}>WGS 84</span>
          </div>
        </div>

        <MissionSummaryPanel wizard={wizard} plan={plan} validation={validation} realtimeConnected={realtimeConnected} selectedDroneId={selectedDroneId} onSelectLane={setSelectedDroneId} />
      </div>
    </AppShell>
  )
}
