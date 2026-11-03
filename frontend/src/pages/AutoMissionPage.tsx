import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { LatLngTuple } from 'leaflet'
import { Polygon } from 'react-leaflet'
import { Plane, Rocket, ShieldCheck, Wand2 } from 'lucide-react'
import * as autoApi from '../api/autoMission'
import type { AutoAvailability, AutoMission, AutoMode } from '../api/autoMission'
import { ApiError } from '../api/client'
import * as geofencesApi from '../api/geofences'
import * as mc from '../api/missionControl'
import { listTemplates } from '../api/missions'
import type { Geofence, MissionTemplate, MissionTemplateDef } from '../api/types'
import { AppShell } from '../components/layout/AppShell'
import { AoiDrawLayer, type DrawMode } from '../components/map/AoiDrawLayer'
import { CbbaLanes } from '../components/map/CbbaLanes'
import { GeofenceLayers } from '../components/map/GeofenceLayers'
import { MissionMap } from '../components/map/MissionMap'
import { formatDuration, formatPct } from '../lib/format'
import { latLngRingToPolygon as toPolygon, polygonBounds, ringToLatLng } from '../lib/mapGeometry'
import { cssVar } from '../lib/tokens'
import { useAuthStore } from '../store/useAuthStore'
import { useDroneStore } from '../store/useDroneStore'

const errText = (err: unknown) => (err instanceof ApiError ? `${err.code ? `${err.code}: ` : ''}${err.message}` : String(err))

const STATE_LABEL: Record<string, { text: string; cls: string }> = {
  uploaded: { text: 'Ready', cls: 'dm-banner' },
  waiting: { text: 'Waiting', cls: 'dm-banner' },
  flying: { text: 'Flying', cls: 'dm-banner dm-banner--ok' },
  completed: { text: 'Done', cls: 'dm-banner dm-banner--ok' },
  dropped: { text: 'Dropped out', cls: 'dm-banner dm-banner--bad' },
  replaced: { text: 'Replaced', cls: 'dm-banner dm-banner--amber' },
}

/**
 * Auto Mission: choose what (mission type), where (draw the area) and how many
 * drones. Drone choice, altitudes, geofence, CBBA lanes, validation, launch and
 * dropout handling are automatic; a person approves the flight once.
 */
export function AutoMissionPage() {
  const navigate = useNavigate()
  const role = useAuthStore((s) => s.user?.role)
  const canWrite = role === 'ADMIN' || role === 'OPERATOR'
  const drones = useDroneStore((s) => s.drones)
  const fetchDrones = useDroneStore((s) => s.fetchDrones)

  const [templates, setTemplates] = useState<MissionTemplateDef[]>([])
  const [template, setTemplate] = useState<MissionTemplate>('survey')
  const [mode, setMode] = useState<AutoMode>('simulation')
  const [count, setCount] = useState(2)
  const [drawMode, setDrawMode] = useState<DrawMode>('rectangle')
  const [drawing, setDrawing] = useState(true)
  const [points, setPoints] = useState<[number, number][]>([])
  const [avail, setAvail] = useState<AutoAvailability | null>(null)
  const [geofences, setGeofences] = useState<Geofence[]>([])
  const [result, setResult] = useState<AutoMission | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [ackText, setAckText] = useState<Record<string, string>>({})
  const [acks, setAcks] = useState<Record<string, boolean>>({})
  const [minutes, setMinutes] = useState(20)

  useEffect(() => {
    void listTemplates().then(setTemplates).catch(() => undefined)
    void mc.flightPermissionAcks().then((r) => setAckText(r.required)).catch(() => undefined)
    void geofencesApi.listGeofences().then(setGeofences).catch(() => undefined)
    void fetchDrones()
  }, [fetchDrones])

  const loadAvailability = useCallback(() => void autoApi.availability().then(setAvail).catch(() => undefined), [])
  useEffect(() => {
    loadAvailability()
    const t = window.setInterval(loadAvailability, 5000)
    return () => window.clearInterval(t)
  }, [loadAvailability])

  // Live status while a real-aircraft launch is in progress.
  const missionId = result?.mission_id
  const live = result?.mode === 'hardware' && !!result.launch?.status && result.launch.status !== 'completed'
  useEffect(() => {
    if (!missionId || !live) return
    const t = window.setInterval(() => void autoApi.getAuto(missionId).then(setResult).catch(() => undefined), 2000)
    return () => window.clearInterval(t)
  }, [missionId, live])

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label)
    setError(null)
    setNotice(null)
    try {
      await fn()
    } catch (err) {
      setError(errText(err))
      if (err instanceof ApiError && err.body && Array.isArray((err.body as { not_ready?: unknown }).not_ready)) {
        const nr = (err.body as { not_ready: { name: string; reason: string }[] }).not_ready
        if (nr.length) setError(`${errText(err)}. Not ready: ${nr.map((n) => `${n.name} (${n.reason})`).join('; ')}`)
      }
    } finally {
      setBusy(null)
    }
  }

  const tpl = templates.find((t) => t.key === template)
  const hw = avail?.hardware[template]
  const maxCount = mode === 'hardware' ? hw?.ready.length ?? 0 : avail?.simulation.available ?? 0
  const aoiOk = (drawMode === 'rectangle' && points.length === 4) || (drawMode === 'polygon' && points.length >= 3)
  const planned = result?.plan ?? null
  const allAcked = Object.keys(ackText).length > 0 && Object.keys(ackText).every((k) => acks[k])
  const fitTo = useMemo(() => (points.length >= 3 ? polygonBounds(toPolygon(points).coordinates[0]) : null), [points])
  const resetPlan = () => setResult(null)

  return (
    <AppShell title="Auto Mission">
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 420px) 1fr', gap: '1rem', minHeight: 0, flex: 1 }} data-testid="auto-mission-page">
        <div style={{ display: 'grid', gap: '.8rem', alignContent: 'start', overflow: 'auto' }}>
          <div className="dm-banner" style={{ fontSize: '.8rem', textTransform: 'none', letterSpacing: 0 }}>
            Choose the mission type, draw the area and say how many drones. Drone MatrX picks the drones, sets safe altitudes and the
            geofence, plans every lane with CBBA and checks everything. You approve once to fly.
          </div>
          {error && <div className="dm-banner dm-banner--bad" style={{ fontSize: '.8rem', textTransform: 'none', letterSpacing: 0 }} data-testid="auto-error">{error}</div>}
          {notice && <div className="dm-banner dm-banner--ok" style={{ fontSize: '.8rem', textTransform: 'none', letterSpacing: 0 }}>{notice}</div>}

          <section className="dm-card" style={{ padding: '.8rem', display: 'grid', gap: '.5rem' }}>
            <div className="dm-card-title" style={{ margin: 0 }}><Wand2 size={13} /> 1. Mission type</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '.4rem' }}>
              {templates.map((t) => (
                <button key={t.key} type="button" disabled={!!result?.launch?.status} onClick={() => { setTemplate(t.key); resetPlan() }}
                  className={`dm-btn ${template === t.key ? 'dm-btn--primary' : ''}`} data-testid={`auto-template-${t.key}`}
                  style={{ textAlign: 'left', display: 'grid', gap: 2, whiteSpace: 'normal', padding: '.45rem .55rem' }}>
                  <span>{t.label}</span>
                  <span style={{ fontSize: '.65rem', opacity: 0.8, textTransform: 'none', letterSpacing: 0 }}>needs {t.required_sensors.join(', ')}</span>
                </button>
              ))}
            </div>
            {tpl && <div style={{ fontSize: '.75rem', opacity: 0.85 }}>{tpl.description}</div>}
          </section>

          <section className="dm-card" style={{ padding: '.8rem', display: 'grid', gap: '.5rem' }}>
            <div className="dm-card-title" style={{ margin: 0 }}>2. Area</div>
            <div style={{ fontSize: '.78rem' }}>
              {drawMode === 'rectangle' ? 'Click two opposite corners on the map.' : 'Click each corner on the map (3 or more).'}
              {aoiOk && <strong data-testid="auto-aoi-ok"> Area ready.</strong>}
            </div>
            <div style={{ display: 'flex', gap: '.4rem', flexWrap: 'wrap' }}>
              {(['rectangle', 'polygon'] as DrawMode[]).map((m) => (
                <button key={m} type="button" className={`dm-btn ${drawMode === m ? 'dm-btn--primary' : ''}`} disabled={!!result?.launch?.status}
                  onClick={() => { setDrawMode(m); setPoints([]); setDrawing(true); resetPlan() }}>{m === 'rectangle' ? 'Rectangle' : 'Polygon'}</button>
              ))}
              <button type="button" className="dm-btn dm-btn--ghost" disabled={!!result?.launch?.status} onClick={() => { setPoints([]); setDrawing(true); resetPlan() }}>Clear</button>
            </div>
          </section>

          <section className="dm-card" style={{ padding: '.8rem', display: 'grid', gap: '.5rem' }}>
            <div className="dm-card-title" style={{ margin: 0 }}><Plane size={13} /> 3. Drones</div>
            <div style={{ display: 'flex', gap: '.4rem' }}>
              {(['simulation', 'hardware'] as AutoMode[]).map((m) => (
                <button key={m} type="button" className={`dm-btn ${mode === m ? 'dm-btn--primary' : ''}`} data-testid={`auto-mode-${m}`}
                  disabled={!!result?.launch?.status} onClick={() => { setMode(m); resetPlan() }}>{m === 'simulation' ? 'Simulation' : 'Real drones'}</button>
              ))}
            </div>
            <label className="dm-field" style={{ display: 'flex', alignItems: 'center', gap: '.5rem' }}>
              <span className="dm-label">Number of drones</span>
              <input className="dm-input" type="number" min={1} max={16} value={count} style={{ width: 80 }} data-testid="auto-count"
                disabled={!!result?.launch?.status} onChange={(e) => { setCount(Math.max(1, Number(e.target.value) || 1)); resetPlan() }} />
              <span style={{ fontSize: '.75rem', opacity: 0.85 }} data-testid="auto-available">{maxCount} available</span>
            </label>
            {mode === 'hardware' && hw && (
              <div style={{ fontSize: '.75rem', display: 'grid', gap: '.2rem' }}>
                {hw.ready.length > 0 && <div>Ready: {hw.ready.join(', ')}</div>}
                {hw.not_ready.filter((n) => !n.reason.startsWith('not linked')).map((n) => (
                  <div key={n.drone_id} style={{ opacity: 0.85 }}>Not ready: {n.name}: {n.reason}</div>
                ))}
                {hw.not_ready.some((n) => n.reason.startsWith('not linked')) && (
                  <details style={{ opacity: 0.75 }}>
                    <summary style={{ cursor: 'pointer' }}>{hw.not_ready.filter((n) => n.reason.startsWith('not linked')).length} other real drone record(s) not linked to a flight controller</summary>
                    {hw.not_ready.filter((n) => n.reason.startsWith('not linked')).map((n) => n.name).join(', ')}. Link them in Drone Setup step 12.
                  </details>
                )}
                {hw.ready.length === 0 && hw.not_ready.length === 0 && <div>No real drones set up yet: use <a href="/drone-setup">Drone Setup</a>.</div>}
                <div style={{ opacity: 0.75 }}>Real drones fly below {avail?.limits.max_alt_m ?? 30} m, each at its own height.</div>
              </div>
            )}
          </section>

          {canWrite && !result?.launch?.status && (
            <button type="button" className="dm-btn dm-btn--primary" disabled={!aoiOk || !!busy || count > maxCount} data-testid="auto-plan"
              onClick={() => run('plan', async () => {
                const r = await autoApi.planAuto({ template, aoi: toPolygon(points), drone_count: count, mode })
                setResult(r)
                setDrawing(false)
              })}>
              {busy === 'plan' ? 'Planning...' : `Plan mission with ${count} drone${count === 1 ? '' : 's'}`}
            </button>
          )}

          {result && (
            <section className="dm-card" style={{ padding: '.8rem', display: 'grid', gap: '.5rem' }} data-testid="auto-result">
              <div className="dm-card-title" style={{ margin: 0 }}><ShieldCheck size={13} /> {result.name}</div>
              {planned && (
                <div style={{ fontSize: '.8rem', display: 'grid', gap: '.15rem' }}>
                  <div>{planned.task_count} lanes on {planned.bundles.length} drones &middot; coverage {formatPct(planned.coverage_pct)} &middot; ETA {formatDuration(planned.estimated_duration_s)}</div>
                  <div>Altitude {result.mode === 'hardware' ? 'per drone (below)' : `${result.flight?.altitude_m} m`} &middot; speed {result.flight?.speed_mps} m/s</div>
                  {result.geofence_id && <div>Geofence created around the area (+30 m).</div>}
                </div>
              )}
              <table className="dm-data-table" style={{ fontSize: '.75rem' }}>
                <tbody>
                  {(result.drones_status?.length ? result.drones_status : Object.entries(result.drones).map(([id, d]) => ({ drone_id: id, name: d.name, altitude_m: d.altitude_m, state: null as string | null, tasks_done: 0, tasks_total: planned?.bundles.find((b) => b.drone_id === id)?.task_count ?? 0, mode: null as string | null, drop_reason: null as string | null, reassigned_to: null as string | null }))).map((d) => (
                    <tr key={d.drone_id} data-testid={`auto-drone-${d.name}`}>
                      <td>{d.name}</td>
                      <td>{d.altitude_m ? `${d.altitude_m} m` : ''}</td>
                      <td>{d.state ? `${d.tasks_done}/${d.tasks_total} lanes` : `${d.tasks_total} lanes`}</td>
                      <td>{d.state && <span className={STATE_LABEL[d.state]?.cls} style={{ fontSize: '.65rem', padding: '.1rem .35rem' }} data-testid={`auto-state-${d.name}`}>{STATE_LABEL[d.state]?.text ?? d.state}</span>}
                        {d.drop_reason && <div style={{ fontSize: '.65rem' }}>{d.drop_reason}</div>}
                        {d.reassigned_to && <div style={{ fontSize: '.65rem' }}>tasks sent to {d.reassigned_to}</div>}
                      </td>
                      <td>{d.state === 'dropped' && canWrite && (
                        <button type="button" className="dm-btn" disabled={!!busy || !allAcked} data-testid={`auto-reassign-${d.name}`}
                          title={allAcked ? 'Send the unfinished lanes to a ready drone on the ground' : 'Confirm the safety checks below first'}
                          onClick={() => run('reassign', async () => {
                            const r = await autoApi.reassignAuto(result.mission_id, d.drone_id, acks)
                            setNotice(`${r.tasks.length} unfinished lane(s) from ${r.from} sent to ${r.to}.`)
                            setResult(await autoApi.getAuto(result.mission_id))
                          })}>Send replacement</button>
                      )}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {result.validation && !result.validation.passed && (
                <div className="dm-banner dm-banner--bad" style={{ fontSize: '.75rem', textTransform: 'none', letterSpacing: 0 }} data-testid="auto-validation-failed">
                  {result.validation.checks.filter((c) => c.severity === 'error').map((c) => c.message).join('; ')}
                </div>
              )}

              {canWrite && result.validation?.passed && result.mode === 'simulation' && !result.launch?.session_id && (
                <button type="button" className="dm-btn dm-btn--primary" disabled={!!busy} data-testid="auto-launch-sim"
                  onClick={() => run('launch', async () => {
                    await autoApi.launchAuto(result.mission_id, {}, minutes)
                    navigate(`/missions/${result.mission_id}/simulate`)
                  })}><Rocket size={13} /> {busy === 'launch' ? 'Preparing simulation...' : 'Launch simulation'}</button>
              )}

              {canWrite && result.validation?.passed && result.mode === 'hardware' && (
                <div style={{ display: 'grid', gap: '.35rem', border: '1px solid #FF8A80', borderRadius: 4, padding: '.5rem' }} data-testid="auto-approval">
                  <div style={{ fontSize: '.75rem', color: '#FF8A80' }}>
                    REAL AIRCRAFT. One approval covers every drone in this mission{result.launch?.status ? ' (and any replacement drone)' : ''}.
                  </div>
                  {Object.entries(ackText).map(([k, text]) => (
                    <label key={k} style={{ display: 'flex', gap: '.4rem', fontSize: '.75rem', alignItems: 'flex-start' }}>
                      <input type="checkbox" checked={!!acks[k]} onChange={(e) => setAcks((a) => ({ ...a, [k]: e.target.checked }))} data-testid={`auto-ack-${k}`} />
                      {text}
                    </label>
                  ))}
                  {!result.launch?.status && (
                    <>
                      <label className="dm-field" style={{ display: 'flex', gap: '.4rem', alignItems: 'center', fontSize: '.75rem' }}>
                        Permission valid for <input className="dm-input" type="number" min={1} max={30} value={minutes} style={{ width: 64 }}
                          onChange={(e) => setMinutes(Math.max(1, Math.min(30, Number(e.target.value) || 1)))} /> minutes
                      </label>
                      <button type="button" className="dm-btn dm-btn--danger" disabled={!allAcked || !!busy} data-testid="auto-launch-hw"
                        onClick={() => run('launch', async () => {
                          const r = await autoApi.launchAuto(result.mission_id, acks, minutes)
                          setNotice(`Launching ${r.start_order?.join(', ')}: one drone every 10 s. The safety pilots can take over at any time.`)
                          setResult(await autoApi.getAuto(result.mission_id))
                        })}><Rocket size={13} /> {busy === 'launch' ? 'Checking every drone...' : `Launch ${Object.keys(result.drones).length} real drones`}</button>
                    </>
                  )}
                </div>
              )}
              {result.launch?.status && result.mode === 'hardware' && (
                <div style={{ fontSize: '.75rem' }} data-testid="auto-launch-status">
                  Mission {result.launch.status}. Emergencies: use <a href="/mission-control">Mission Control</a> (RTL ALL / LAND ALL) or the RC transmitters.
                </div>
              )}
            </section>
          )}
        </div>

        <div style={{ position: 'relative', minHeight: 420 }} data-testid="auto-map">
          <MissionMap drones={drones} fitTo={fitTo} fitKey={result ? result.mission_id : null}>
            <GeofenceLayers geofences={geofences} />
            {aoiOk && (
              <Polygon positions={ringToLatLng(toPolygon(points).coordinates[0])}
                pathOptions={{ color: cssVar('--dm-aoi-stroke'), fillColor: cssVar('--dm-navy-900'), fillOpacity: 0.2, weight: 2, dashArray: planned ? '4 4' : undefined }} />
            )}
            {planned && <CbbaLanes bundles={planned.bundles} tasks={planned.tasks} />}
            <AoiDrawLayer active={drawing && !result && canWrite} mode={drawMode} points={points as LatLngTuple[]}
              onAddPoint={(p) => setPoints((prev) => [...prev, p as [number, number]])}
              onReplacePoints={(pts) => setPoints(pts as [number, number][])} />
          </MissionMap>
        </div>
      </div>
    </AppShell>
  )
}
