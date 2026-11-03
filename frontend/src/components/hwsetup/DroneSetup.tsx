import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { CheckCircle2, Circle, CircleDashed, CircleSlash, RefreshCw } from 'lucide-react'
import { Link } from 'react-router-dom'
import * as mc from '../../api/missionControl'
import type { HwController, RadioInfo, SetupView } from '../../api/missionControl'
import { addPayload, createDrone, listDrones } from '../../api/drones'
import type { Drone } from '../../api/types'
import { ApiError } from '../../api/client'
import {
  FRAME_CLASSES, FRAME_TYPES, MODE_NAMES, explainPrearm, RECOMMENDED_MODES, STEPS, estimateCells, motorCount, progress, safetyValues, stepStatus,
  suggestedSystemId, type StepId, type StepStatus,
} from './setupSteps'

const errText = (err: unknown) => (err instanceof ApiError ? `${err.code ? `${err.code}: ` : ''}${err.message}` : String(err))
const POLL_MS = 1500

const ICON: Record<StepStatus, ReactNode> = {
  done: <CheckCircle2 size={15} color="#69F0AE" />,
  todo: <Circle size={15} color="#FFB74D" />,
  optional: <CircleDashed size={15} color="#90A4AE" />,
  skip: <CircleSlash size={15} color="#607D8B" />,
}
const STATUS_TEXT: Record<StepStatus, string> = { done: 'Done', todo: 'To do', optional: 'Optional', skip: 'Not needed' }

function Note({ children, tone = 'info' }: { children: ReactNode; tone?: 'info' | 'warn' | 'bad' | 'ok' }) {
  const cls = { info: 'dm-banner', warn: 'dm-banner dm-banner--amber', bad: 'dm-banner dm-banner--bad', ok: 'dm-banner dm-banner--ok' }[tone]
  return <div className={cls} style={{ fontSize: '.8rem', textTransform: 'none', letterSpacing: 0 }}>{children}</div>
}

function Steps({ items }: { items: ReactNode[] }) {
  return <ol style={{ margin: 0, paddingLeft: '1.2rem', display: 'grid', gap: '.3rem', fontSize: '.85rem' }}>{items.map((x, i) => <li key={i}>{x}</li>)}</ol>
}

/** One flight controller, set up step by step. */
export function DroneSetup({ controller, canWrite, onFcIdChange }: {
  controller: HwController
  canWrite: boolean
  onFcIdChange: (fcId: string) => void
}) {
  const fcId = controller.fc_id
  const [view, setView] = useState<SetupView | null>(null)
  const [step, setStep] = useState<StepId>('identity')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const refreshNext = useRef(true)

  const load = useCallback(async () => {
    try {
      const v = await mc.setupView(fcId, refreshNext.current)
      refreshNext.current = false
      setView(v)
      setLoadError(null)
    } catch (err) {
      setLoadError(errText(err))
    }
  }, [fcId])

  // The panel is keyed by fc_id, so each controller starts with a fresh state.
  useEffect(() => {
    void load()
    const t = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(t)
  }, [load])

  const run = async (label: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(label)
    setError(null)
    setNotice(null)
    try {
      await fn()
      if (done) setNotice(done)
      refreshNext.current = true
      await load()
    } catch (err) {
      setError(errText(err))
    } finally {
      setBusy(null)
    }
  }

  if (!view) {
    return (
      <div className="dm-card" style={{ padding: '1rem' }} data-testid="setup-loading">
        {loadError ? <Note tone="bad">Cannot read this flight controller: {loadError}</Note> : 'Reading the flight controller settings (a few seconds over a radio)...'}
      </div>
    )
  }

  const bound = controller.bound
  const prog = progress(view, bound)
  const current = STEPS.find((s) => s.id === step)!
  const locked = !canWrite || view.armed
  const props = { view, busy, locked, run, fcId }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 280px) 1fr', gap: '1rem', alignItems: 'start' }} data-testid="drone-setup">
      <div className="dm-card" style={{ padding: '.6rem', display: 'grid', gap: '.2rem' }}>
        <div style={{ fontSize: '.8rem', opacity: 0.8, padding: '.2rem .4rem' }} data-testid="setup-progress">
          {prog.done} of {prog.required} required steps done
        </div>
        {STEPS.map((s, i) => {
          const st = stepStatus(s.id, view, bound)
          return (
            <button key={s.id} type="button" onClick={() => setStep(s.id)} data-testid={`setup-step-${s.id}`} data-status={st}
              style={{ display: 'flex', gap: '.5rem', alignItems: 'center', textAlign: 'left', border: 0, borderRadius: 4, padding: '.45rem .5rem',
                color: 'inherit', cursor: 'pointer', background: s.id === step ? 'color-mix(in srgb, var(--dm-slate-700) 60%, transparent)' : 'transparent' }}>
              {ICON[st]}
              <span style={{ flex: 1, fontSize: '.85rem' }}>{i + 1}. {s.title}</span>
              <span style={{ fontSize: '.7rem', opacity: 0.7 }}>{STATUS_TEXT[st]}</span>
            </button>
          )
        })}
      </div>

      <div className="dm-card" style={{ padding: '1rem', display: 'grid', gap: '.8rem' }} data-testid={`setup-panel-${step}`}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '.5rem' }}>
          <div>
            <div className="dm-card-title" style={{ margin: 0 }}>{current.title}</div>
            <div style={{ fontSize: '.85rem', opacity: 0.8 }}>{current.summary}</div>
          </div>
          <button type="button" className="dm-btn dm-btn--ghost" title="Re-read settings from the drone" onClick={() => run('reload', async () => undefined)}>
            <RefreshCw size={14} />
          </button>
        </div>
        {view.armed && <Note tone="bad">This drone is ARMED. Setup is disabled until it is disarmed on the ground.</Note>}
        {!canWrite && <Note tone="warn">Only operators and admins can change a drone. You can look but not change.</Note>}
        {error && <Note tone="bad"><span data-testid="setup-error">{error}</span></Note>}
        {notice && <Note tone="ok"><span data-testid="setup-notice">{notice}</span></Note>}

        {step === 'identity' && <IdentityStep {...props} onFcIdChange={onFcIdChange} bound={bound} />}
        {step === 'frame' && <FrameStep {...props} />}
        {step === 'radio' && <RadioStep {...props} />}
        {step === 'accel' && <AccelStep {...props} />}
        {step === 'compass' && <CompassStep {...props} />}
        {step === 'rc' && <RcStep {...props} />}
        {step === 'modes' && <ModesStep {...props} />}
        {step === 'safety' && <SafetyStep {...props} />}
        {step === 'motors' && <MotorStep {...props} />}
        {step === 'camera' && <CameraStep {...props} />}
        {step === 'check' && <CheckStep {...props} />}
        {step === 'register' && <RegisterStep {...props} controller={controller} />}

        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <button type="button" className="dm-btn dm-btn--ghost" disabled={STEPS[0].id === step}
            onClick={() => setStep(STEPS[Math.max(0, STEPS.findIndex((s) => s.id === step) - 1)].id)}>Back</button>
          <button type="button" className="dm-btn" disabled={STEPS[STEPS.length - 1].id === step} data-testid="setup-next"
            onClick={() => setStep(STEPS[Math.min(STEPS.length - 1, STEPS.findIndex((s) => s.id === step) + 1)].id)}>Next step</button>
        </div>
      </div>
    </div>
  )
}

interface StepProps {
  view: SetupView
  busy: string | null
  locked: boolean
  fcId: string
  run: (label: string, fn: () => Promise<unknown>, done?: string) => Promise<void>
}

function Btn({ label, busy, locked, onClick, primary = true, testid, disabled }: {
  label: string; busy: string | null; locked: boolean; onClick: () => void; primary?: boolean; testid?: string; disabled?: boolean
}) {
  return (
    <button type="button" className={`dm-btn ${primary ? 'dm-btn--primary' : ''}`} disabled={locked || !!busy || disabled} onClick={onClick} data-testid={testid}>
      {busy === label ? 'Working...' : label}
    </button>
  )
}

function IdentityStep({ view, busy, locked, run, fcId, onFcIdChange, bound }: StepProps & { onFcIdChange: (id: string) => void; bound: boolean }) {
  const suggested = suggestedSystemId(view)
  const [id, setId] = useState(suggested)
  const duplicate = view.other_system_ids.includes(view.system_id)
  return (
    <>
      <div style={{ fontSize: '.95rem' }}>This drone's ID is <strong data-testid="setup-sysid">{view.system_id}</strong>.</div>
      {duplicate
        ? <Note tone="bad">Another connected drone also uses ID {view.system_id}. Give this one a different ID.</Note>
        : <Note tone="ok">No other connected drone uses ID {view.system_id}.</Note>}
      {view.other_system_ids.length > 0 && <div style={{ fontSize: '.8rem', opacity: 0.8 }}>IDs used by the other connected drones: {view.other_system_ids.join(', ')}</div>}
      {bound && <Note tone="warn">This drone is linked to a Drone MatrX drone record. Unlink it in Mission Control before changing its ID.</Note>}
      <Steps items={[
        'Connect ALL your drones first (step 2 above), so Drone MatrX can see which IDs are taken.',
        'Choose an ID that no other drone uses (1, 2, 3 ...). Write it on the drone with tape.',
        'Press "Set ID & restart". The drone restarts and appears again with its new ID in about 15 seconds.',
      ]} />
      <label className="dm-field" style={{ display: 'flex', gap: '.5rem', alignItems: 'center' }}>
        <span className="dm-label">New ID</span>
        <input className="dm-input" type="number" min={1} max={250} value={id} style={{ width: 90 }} data-testid="setup-sysid-input"
          onChange={(e) => setId(Number(e.target.value))} />
        {view.other_system_ids.includes(id) && <span style={{ color: '#FF8A80', fontSize: '.8rem' }}>already used</span>}
      </label>
      <div>
        <Btn label="Set ID & restart" busy={busy} locked={locked || bound} testid="setup-sysid-apply"
          disabled={id === view.system_id || id < 1 || id > 250 || view.other_system_ids.includes(id)}
          onClick={() => run('Set ID & restart', async () => {
            const r = await mc.setSystemId(fcId, id)
            onFcIdChange(r.new_fc_id)
          }, `ID set to ${id}. The drone is restarting...`)} />
      </div>
    </>
  )
}

function FrameStep({ view, busy, locked, run, fcId }: StepProps) {
  const [cls, setCls] = useState(view.params.FRAME_CLASS || 1)
  const [type, setType] = useState(view.params.FRAME_TYPE ?? 1)
  return (
    <>
      <Steps items={['Pick how many motors your drone has.', 'Pick the arm layout. Most quadcopters are "X".', 'Press "Save frame". A restart is needed afterwards (step 11).']} />
      <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
        <label className="dm-field"><span className="dm-label">Motors</span>
          <select className="dm-input" value={cls} onChange={(e) => setCls(Number(e.target.value))} data-testid="setup-frame-class">
            {FRAME_CLASSES.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
        </label>
        <label className="dm-field"><span className="dm-label">Layout</span>
          <select className="dm-input" value={type} onChange={(e) => setType(Number(e.target.value))}>
            {FRAME_TYPES.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
          </select>
        </label>
      </div>
      <div><Btn label="Save frame" busy={busy} locked={locked} testid="setup-frame-apply"
        onClick={() => run('Save frame', () => mc.writeParams(fcId, { FRAME_CLASS: cls, FRAME_TYPE: type }), 'Frame saved. Restart the drone in step 11 when you finish.')} /></div>
    </>
  )
}

function RadioStep({ view, busy, locked, run }: StepProps) {
  const [info, setInfo] = useState<RadioInfo | null>(null)
  const [netid, setNetid] = useState<number | null>(null)
  if (!view.port || !view.telemetry_radio) {
    return <Note>This drone is connected by {view.port ? 'USB cable' : 'network'}, not a telemetry radio, so there is nothing to pair. Continue with the next step.</Note>
  }
  return (
    <>
      <div style={{ fontSize: '.9rem' }}>Ground radio: <strong>{view.port}</strong>{view.radio_netid !== null && <> &middot; paired with network ID <strong>{view.radio_netid}</strong></>}</div>
      <Steps items={[
        'Keep the drone powered on: its air radio must be on and linked (solid green light).',
        'Press "Read radio". Drone MatrX briefly pauses this connection to talk to the radios.',
        'Press "Pair" with a network ID no other radio uses. Both radios save it and restart (about 5 seconds).',
      ]} />
      <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap' }}>
        <Btn label="Read radio" primary={false} busy={busy} locked={locked} testid="setup-radio-read"
          onClick={() => run('Read radio', async () => {
            const r = await mc.radioInfo(view.port!)
            setInfo(r)
            setNetid(r.suggested_netid)
          })} />
      </div>
      {info && (
        <div className="dm-kv" style={{ fontSize: '.85rem', display: 'grid', gap: '.2rem' }}>
          <div>Radio: {info.version ?? 'unknown'}</div>
          <div>Ground radio network ID: {info.local.NETID ?? '?'} &middot; Air radio: {info.remote_reachable ? info.remote?.NETID : <span style={{ color: '#FF8A80' }}>not answering (is the drone on?)</span>}</div>
          {Object.keys(info.netids_in_use).length > 0 && <div>Already used: {Object.entries(info.netids_in_use).map(([p, n]) => `${n} (${p})`).join(', ')}</div>}
          <label className="dm-field" style={{ display: 'flex', gap: '.5rem', alignItems: 'center' }}>
            <span className="dm-label">Network ID</span>
            <input className="dm-input" type="number" min={0} max={499} value={netid ?? ''} style={{ width: 90 }} onChange={(e) => setNetid(Number(e.target.value))} />
            <Btn label="Pair" busy={busy} locked={locked} disabled={netid === null || !info.remote_reachable}
              onClick={() => run('Pair', () => mc.setRadioNetid(view.port!, netid!), `Both radios now use network ID ${netid}.`)} />
          </label>
        </div>
      )}
    </>
  )
}

function AccelStep({ view, busy, locked, run, fcId }: StepProps) {
  const cal = view.accelcal
  const active = cal.state === 'waiting' || cal.state === 'measuring' || cal.state === 'starting'
  const HOW: Record<string, string> = {
    LEVEL: 'Put the drone flat on a level surface, the way it sits on the ground.',
    LEFT: 'Put the drone on its LEFT side (left arm pointing down).',
    RIGHT: 'Put the drone on its RIGHT side (right arm pointing down).',
    'NOSE DOWN': 'Point the nose (front) straight DOWN.',
    'NOSE UP': 'Point the nose (front) straight UP.',
    BACK: 'Put the drone UPSIDE DOWN on its back.',
  }
  return (
    <>
      <Note tone="warn">Propellers OFF. Hold the drone still in each position; the flight controller measures for a few seconds.</Note>
      {!active && <Steps items={['Press "Start calibration".', 'Put the drone in the position shown, hold it still, press "Done, next position".', 'Repeat for all six positions.']} />}
      {active && (
        <div style={{ display: 'grid', gap: '.5rem' }} data-testid="setup-accel-active">
          <div style={{ fontSize: '1.4rem', fontWeight: 600 }} data-testid="setup-accel-position">{cal.position ?? 'Starting...'}</div>
          {cal.position && <div style={{ fontSize: '.95rem' }}>{HOW[cal.position]}</div>}
          <div>
            <Btn label="Done, next position" busy={busy} locked={locked} testid="setup-accel-next" disabled={cal.state !== 'waiting'}
              onClick={() => run('Done, next position', () => mc.calibrate(fcId, 'accel-next'))} />
            {cal.state === 'measuring' && <span style={{ marginLeft: '.6rem', fontSize: '.85rem' }}>Measuring, hold still...</span>}
          </div>
        </div>
      )}
      {cal.state === 'success' && <Note tone="ok"><span data-testid="setup-accel-result">{cal.message}</span></Note>}
      {cal.state === 'failed' && <Note tone="bad">{cal.message}</Note>}
      {!active && (
        <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap' }}>
          <Btn label="Start calibration" busy={busy} locked={locked} testid="setup-accel-start" onClick={() => run('Start calibration', () => mc.calibrate(fcId, 'accel-start'))} />
          <Btn label="Level only (quick)" primary={false} busy={busy} locked={locked}
            onClick={() => run('Level only (quick)', () => mc.calibrate(fcId, 'level'), 'Level saved. Use this only to correct a drone that drifts after a full calibration.')} />
        </div>
      )}
    </>
  )
}

function CompassStep({ view, busy, locked, run, fcId }: StepProps) {
  const entries = Object.entries(view.magcal)
  const running = entries.some(([, m]) => m.status.startsWith('RUNNING') || m.status === 'WAITING_TO_START')
  const success = entries.length > 0 && entries.every(([, m]) => m.status === 'SUCCESS')
  const failed = entries.some(([, m]) => ['FAILED', 'BAD_ORIENTATION', 'BAD_RADIUS'].includes(m.status))
  return (
    <>
      <Note tone="warn">Do this outdoors, away from cars, metal tables and reinforced concrete. Propellers OFF, battery connected.</Note>
      <Steps items={[
        'Press "Start compass calibration".',
        'Pick the drone up and slowly rotate it so every side points at the ground once: nose down, tail down, left, right, top, bottom. Keep turning until the bar is full.',
        'When it says SUCCESS the result is saved automatically. Restart the drone in step 11.',
      ]} />
      {entries.map(([id, m]) => (
        <div key={id} style={{ display: 'grid', gap: '.25rem' }} data-testid={`setup-compass-${id}`}>
          <div style={{ fontSize: '.85rem' }}>Compass {Number(id) + 1}: {m.status}{m.fitness !== undefined && ` (fitness ${m.fitness})`}</div>
          <div style={{ height: 10, background: 'rgb(255 255 255 / .12)', borderRadius: 5 }}>
            <div style={{ height: '100%', width: `${m.pct}%`, borderRadius: 5, background: m.status === 'SUCCESS' ? '#69F0AE' : '#4FC3F7', transition: 'width .4s' }} />
          </div>
        </div>
      ))}
      {success && <Note tone="ok">Compass calibration successful and saved.</Note>}
      {failed && <Note tone="bad">Calibration failed. Move away from metal and try again, rotating more slowly.</Note>}
      <div style={{ display: 'flex', gap: '.5rem' }}>
        <Btn label="Start compass calibration" busy={busy} locked={locked} disabled={running} testid="setup-compass-start"
          onClick={() => run('Start compass calibration', () => mc.calibrate(fcId, 'compass-start'))} />
        {running && <Btn label="Cancel" primary={false} busy={busy} locked={locked} onClick={() => run('Cancel', () => mc.calibrate(fcId, 'compass-cancel'))} />}
      </div>
    </>
  )
}

const STICKS = ['Roll (right stick left/right)', 'Pitch (right stick up/down)', 'Throttle (left stick up/down)', 'Yaw (left stick left/right)']

function RcStep({ view, busy, locked, run, fcId }: StepProps) {
  const [rec, setRec] = useState<{ min: number[]; max: number[] } | null>(null)
  const ch = view.rc_channels
  const live = ch && (view.rc_age_s ?? 99) < 3
  useEffect(() => {
    if (!rec || !ch) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- accumulate stick extremes from each new RC sample
    setRec((r) => r && {
      min: r.min.map((m, i) => (ch[i] > 800 ? Math.min(m, ch[i]) : m)),
      max: r.max.map((m, i) => (ch[i] > 800 ? Math.max(m, ch[i]) : m)),
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- record on every new RC sample
  }, [ch?.join(',')])
  const ranges = rec ? [0, 1, 2, 3].map((i) => rec.max[i] - rec.min[i]) : []
  const enough = ranges.length === 4 && ranges.every((r) => r > 600)

  const finish = () => run('Save calibration', async () => {
    if (!rec || !ch) return
    const values: Record<string, number> = {}
    for (let i = 0; i < 4; i++) {
      const n = i + 1
      const min = rec.min[i], max = rec.max[i]
      // Sticks centred now (throttle down): trim = current value; throttle trim = its minimum.
      const trim = n === 3 ? min : Math.min(max - 10, Math.max(min + 10, ch[i]))
      values[`RC${n}_MIN`] = min
      values[`RC${n}_MAX`] = max
      values[`RC${n}_TRIM`] = trim
    }
    await mc.writeParams(fcId, values)
    setRec(null)
  }, 'RC calibration saved.')

  return (
    <>
      {!live && <Note tone="bad">No RC signal. Turn the transmitter on and make sure it is bound to the drone's receiver.</Note>}
      <Steps items={[
        'Turn the transmitter on. Put all switches in their normal position, throttle DOWN.',
        'Press "Start". Move both sticks slowly to every corner and all switches through every position.',
        'When every bar shows a green range, centre the sticks (throttle DOWN) and press "Save calibration".',
      ]} />
      <div style={{ display: 'grid', gap: '.35rem' }} data-testid="setup-rc-bars">
        {(ch ?? []).slice(0, 8).map((v, i) => {
          const pct = Math.max(0, Math.min(100, ((v - 900) / 1200) * 100))
          const lo = rec ? ((rec.min[i] - 900) / 1200) * 100 : null
          const hi = rec ? ((rec.max[i] - 900) / 1200) * 100 : null
          return (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: '210px 1fr 60px', gap: '.5rem', alignItems: 'center', fontSize: '.78rem' }}>
              <span>{i < 4 ? STICKS[i] : `Channel ${i + 1}${i + 1 === (view.params.FLTMODE_CH ?? 5) ? ' (mode switch)' : ''}`}</span>
              <div style={{ position: 'relative', height: 10, background: 'rgb(255 255 255 / .1)', borderRadius: 5 }}>
                {rec && i < 4 && lo !== null && hi !== null && (
                  <div style={{ position: 'absolute', left: `${lo}%`, width: `${Math.max(0, hi - lo)}%`, top: 0, bottom: 0, borderRadius: 5,
                    background: ranges[i] > 600 ? 'rgb(105 240 174 / .45)' : 'rgb(255 183 77 / .45)' }} />
                )}
                <div style={{ position: 'absolute', left: `calc(${pct}% - 2px)`, width: 4, top: -2, bottom: -2, background: '#4FC3F7', borderRadius: 2 }} />
              </div>
              <span style={{ fontFamily: 'monospace' }}>{v}</span>
            </div>
          )
        })}
      </div>
      <div style={{ display: 'flex', gap: '.5rem' }}>
        {!rec
          ? <Btn label="Start" busy={busy} locked={locked || !live} testid="setup-rc-start"
            onClick={() => ch && setRec({ min: ch.slice(0, 4), max: ch.slice(0, 4) })} />
          : <>
            <Btn label="Save calibration" busy={busy} locked={locked} disabled={!enough} testid="setup-rc-save" onClick={finish} />
            <Btn label="Cancel" primary={false} busy={busy} locked={false} onClick={() => setRec(null)} />
            {!enough && <span style={{ fontSize: '.8rem', alignSelf: 'center' }}>Keep moving the sticks to their limits...</span>}
          </>}
      </div>
    </>
  )
}

function ModesStep({ view, busy, locked, run, fcId }: StepProps) {
  const chan = view.params.FLTMODE_CH ?? 5
  const pwm = view.rc_channels?.[chan - 1] ?? null
  // ArduCopter mode slots by PWM: <=1230, <=1360, <=1490, <=1620, <=1749, above
  const slot = pwm === null ? null : pwm <= 1230 ? 1 : pwm <= 1360 ? 2 : pwm <= 1490 ? 3 : pwm <= 1620 ? 4 : pwm <= 1749 ? 5 : 6
  return (
    <>
      <div style={{ fontSize: '.9rem' }}>
        The safety pilot uses the flight mode switch (channel {chan}) to take control at any time. The recommended setup on a 3-position switch:
        <strong> down = ALT_HOLD</strong> (pilot flies, height held), <strong>middle = LOITER</strong> (holds position),
        <strong> up = RTL</strong> (returns home).
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: '.3rem', fontSize: '.78rem' }} data-testid="setup-modes">
        {[1, 2, 3, 4, 5, 6].map((i) => {
          const m = view.params[`FLTMODE${i}`]
          return (
            <div key={i} style={{ border: '1px solid rgb(255 255 255 / .2)', borderRadius: 4, padding: '.35rem', textAlign: 'center',
              background: slot === i ? 'rgb(79 195 247 / .25)' : 'transparent' }}>
              <div style={{ opacity: 0.7 }}>Position {i}</div>
              <strong>{m === null || m === undefined ? '?' : MODE_NAMES[m] ?? `mode ${m}`}</strong>
            </div>
          )
        })}
      </div>
      <div style={{ fontSize: '.8rem', opacity: 0.8 }}>{slot ? `The switch is in position ${slot} now. Flip it to check each mode lights up.` : 'No RC signal: turn the transmitter on to see the switch position.'}</div>
      <div><Btn label="Use recommended modes" busy={busy} locked={locked} testid="setup-modes-apply"
        onClick={() => run('Use recommended modes', () => mc.writeParams(fcId, RECOMMENDED_MODES), 'Flight modes saved: ALT_HOLD / LOITER / RTL.')} /></div>
    </>
  )
}

function SafetyStep({ view, busy, locked, run, fcId }: StepProps) {
  const [cells, setCells] = useState<number | null>(estimateCells(view.battery_v))
  const values = safetyValues(view, cells)
  const monitor = view.params.BATT_MONITOR
  const rows: [string, string][] = [
    ['Laptop / radio link lost for 5 s', 'Return home (FS_GCS_ENABLE)'],
    ['RC transmitter lost', 'Return home (FS_THR_ENABLE)'],
    ['Battery low', 'Return home (BATT_FS_LOW_ACT)'],
    ['Battery critical', 'Land immediately (BATT_FS_CRT_ACT)'],
    ['Leaves 150 m circle or climbs above 40 m', 'Return home (FENCE)'],
    ['Return height for this drone', `${values.RTL_ALT / 100} m (RTL_ALT; different per drone)`],
  ]
  return (
    <>
      <table className="dm-data-table" style={{ fontSize: '.82rem' }}>
        <tbody>{rows.map(([a, b]) => <tr key={a}><td>{a}</td><td>{b}</td></tr>)}</tbody>
      </table>
      {monitor === 0 && (
        <Note tone="warn">
          The battery monitor is off, so the drone cannot see its battery. If your drone has a standard power module, enable it and restart.
          <div style={{ marginTop: '.4rem' }}><Btn label="Enable battery monitor" primary={false} busy={busy} locked={locked}
            onClick={() => run('Enable battery monitor', () => mc.writeParams(fcId, { BATT_MONITOR: 4 }), 'Battery monitor enabled. Restart the drone (step 11).')} /></div>
        </Note>
      )}
      <label className="dm-field" style={{ display: 'flex', gap: '.5rem', alignItems: 'center' }}>
        <span className="dm-label">Battery cells (S)</span>
        <input className="dm-input" type="number" min={1} max={14} value={cells ?? ''} style={{ width: 80 }} onChange={(e) => setCells(e.target.value ? Number(e.target.value) : null)} />
        <span style={{ fontSize: '.8rem', opacity: 0.8 }}>
          {view.battery_v ? `${view.battery_v.toFixed(1)} V now` : 'voltage not reported'}
          {cells ? ` · low warning at ${values.BATT_LOW_VOLT} V, critical at ${values.BATT_CRT_VOLT} V` : ' · enter the cell count (4S = 4)'}
        </span>
      </label>
      <div><Btn label="Apply safety settings" busy={busy} locked={locked} testid="setup-safety-apply"
        onClick={() => run('Apply safety settings', () => mc.writeParams(fcId, values), 'Safety settings saved and confirmed by the drone.')} /></div>
    </>
  )
}

function MotorStep({ view, busy, locked, run, fcId }: StepProps) {
  const [propsOff, setPropsOff] = useState(false)
  const n = motorCount(view)
  return (
    <>
      <Note tone="bad">REMOVE ALL PROPELLERS before testing motors. A motor test spins the motor even though the drone is disarmed.</Note>
      <label style={{ display: 'flex', gap: '.5rem', alignItems: 'center', fontSize: '.9rem' }}>
        <input type="checkbox" checked={propsOff} onChange={(e) => setPropsOff(e.target.checked)} data-testid="setup-props-off" />
        All propellers are removed and nobody is touching the drone.
      </label>
      <div style={{ fontSize: '.85rem' }}>Each button spins one motor at 7 % for 2 seconds. Motor A is front-right on an X quad, then the order goes clockwise.</div>
      <div style={{ display: 'flex', gap: '.4rem', flexWrap: 'wrap' }}>
        {Array.from({ length: n }, (_, i) => (
          <Btn key={i} label={`Motor ${String.fromCharCode(65 + i)}`} primary={false} busy={busy} locked={locked || !propsOff} testid={`setup-motor-${i + 1}`}
            onClick={() => run(`Motor ${String.fromCharCode(65 + i)}`, () => mc.motorTest(fcId, i + 1, 7, 2), `Motor ${String.fromCharCode(65 + i)} spun.`)} />
        ))}
      </div>
    </>
  )
}

function CameraStep({ view, busy, locked, run }: StepProps) {
  const [url, setUrl] = useState(view.camera_url ?? '')
  return (
    <>
      <Steps items={[
        'Connect the drone camera (or its companion computer) to the same Wi-Fi as this laptop.',
        "Find its RTSP address in the camera's app or manual, e.g. rtsp://192.168.1.101:8554/live. Test it in VLC if unsure.",
        'Paste it here and press "Save camera". It appears on the Mission Control video wall after the drone is added.',
      ]} />
      <input className="dm-input" placeholder="rtsp://192.168.1.101:8554/live" value={url} onChange={(e) => setUrl(e.target.value)} data-testid="setup-camera-url" />
      <div style={{ display: 'flex', gap: '.5rem' }}>
        <Btn label="Save camera" busy={busy} locked={locked} testid="setup-camera-save" disabled={!/^(rtsps?|rtmp|srt|https?):\/\/\S{3,}$/.test(url.trim())}
          onClick={() => run('Save camera', () => mc.setCamera(view.system_id, url.trim()), 'Camera saved.')} />
        {view.camera_url && <Btn label="Remove" primary={false} busy={busy} locked={locked} onClick={() => run('Remove', () => mc.setCamera(view.system_id, null), 'Camera removed.')} />}
      </div>
    </>
  )
}

function CheckStep({ view, busy, locked, run, fcId }: StepProps) {
  const online = (view.heartbeat_age_s ?? 99) < 5
  return (
    <>
      <Steps items={[
        'Press "Restart flight controller" so frame and calibration changes take effect.',
        'Wait about 15 seconds. Outdoors, give the GPS a few minutes to find satellites.',
        'Fix anything listed below until the list is empty.',
      ]} />
      <div><Btn label="Restart flight controller" busy={busy} locked={locked} testid="setup-reboot"
        onClick={() => run('Restart flight controller', () => mc.rebootFc(fcId), 'Restarting. It reconnects in about 15 seconds.')} /></div>
      <div style={{ fontSize: '.85rem' }}>
        Status: {online ? 'connected' : 'not connected (restarting?)'} &middot; GPS: {view.gps_fix === null ? 'unknown' : view.gps_fix >= 3 ? `3D fix, ${view.satellites} satellites` : `no fix yet (${view.satellites ?? 0} satellites)`}
        {view.battery_v !== null && <> &middot; battery {view.battery_v.toFixed(1)} V</>}
      </div>
      {view.prearm_messages.length === 0
        ? online && <Note tone="ok"><span data-testid="setup-check-ok">The flight controller reports no pre-arm problems.</span></Note>
        : (
          <Note tone="bad">
            <div>The flight controller reports:</div>
            <ul style={{ margin: '.3rem 0 0', paddingLeft: '1.2rem' }} data-testid="setup-prearm-list">{view.prearm_messages.map((m) => <li key={m}>{explainPrearm(m)}</li>)}</ul>
          </Note>
        )}
    </>
  )
}

function RegisterStep({ view, busy, locked, run, controller }: StepProps & { controller: HwController }) {
  const [drones, setDrones] = useState<Drone[]>([])
  const [choice, setChoice] = useState('new')
  const [name, setName] = useState(`HW-${String(view.system_id).padStart(2, '0')}`)
  const [uin, setUin] = useState(`DMX-HW-${String(view.system_id).padStart(2, '0')}`)
  useEffect(() => {
    void listDrones().then((r) => setDrones(r.items.filter((d) => d.hardware))).catch(() => undefined)
  }, [controller.bound])
  if (controller.bound) {
    return (
      <>
        <Note tone="ok"><span data-testid="setup-registered">This flight controller is linked to Drone MatrX{controller.uin ? ` as ${controller.uin}` : ''}.</span></Note>
        <div style={{ fontSize: '.9rem' }}>Next: open <Link to="/mission-control">Mission Control</Link>, select the drone, check the pre-arm list and grant a flight permission.</div>
      </>
    )
  }
  const conn = controller.endpoint.startsWith('serial:') ? 'serial' : 'udp'
  return (
    <>
      <Steps items={[
        'Choose "New drone" (or an existing real-aircraft record).',
        'Press "Add & link". The drone appears in Fleet and Mission Control, marked as a REAL aircraft.',
      ]} />
      <label className="dm-field"><span className="dm-label">Drone record</span>
        <select className="dm-input" value={choice} onChange={(e) => setChoice(e.target.value)} data-testid="setup-register-choice">
          <option value="new">New drone</option>
          {drones.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.uin})</option>)}
        </select>
      </label>
      {choice === 'new' && (
        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
          <label className="dm-field"><span className="dm-label">Name</span><input className="dm-input" value={name} onChange={(e) => setName(e.target.value)} data-testid="setup-register-name" /></label>
          <label className="dm-field"><span className="dm-label">UIN / registration</span><input className="dm-input" value={uin} onChange={(e) => setUin(e.target.value)} data-testid="setup-register-uin" /></label>
        </div>
      )}
      <div><Btn label="Add & link" busy={busy} locked={locked} testid="setup-register-apply" disabled={choice === 'new' && (!name.trim() || !uin.trim())}
        onClick={() => run('Add & link', async () => {
          let droneId = choice
          if (choice === 'new') {
            const frame = FRAME_CLASSES.find((f) => f.value === view.params.FRAME_CLASS)?.label.split(' ')[0] ?? 'Multirotor'
            const d = await createDrone({ name: name.trim(), uin: uin.trim(), model: `${frame} (ArduCopter)`, firmware: controller.firmware, hardware: true })
            droneId = d.id
            // A working camera lets the CBBA planner assign camera tasks to this drone.
            if (view.camera_url) await addPayload(d.id, { type: 'camera', name: 'Drone camera', status: 'calibrated' })
          }
          const b = await mc.bindFc(droneId, conn, controller.identity, 'discovery', controller.system_id)
          if (b.state !== 'bound') throw new Error(`Linking failed: ${b.error ?? b.state}`)
        }, 'Added to Drone MatrX and linked.')} /></div>
    </>
  )
}
