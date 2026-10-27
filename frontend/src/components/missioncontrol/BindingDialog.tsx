import { useEffect, useRef, useState } from 'react'
import QRCode from 'qrcode'
import jsQR from 'jsqr'
import { Camera, QrCode, Radar, X } from 'lucide-react'
import * as mc from '../../api/missionControl'
import type { DiscoveredFc, Discovery } from '../../api/missionControl'
import type { LiveDrone } from './liveFleet'

type Tab = 'discover' | 'qr' | 'manual'

interface Props {
  drones: LiveDrone[]
  initialDroneId: string | null
  benches: Record<string, string>
  onClose: () => void
  onBound: () => void
}

export function BindingDialog({ drones, initialDroneId, benches, onClose, onBound }: Props) {
  const [tab, setTab] = useState<Tab>('discover')
  const [droneId, setDroneId] = useState(initialDroneId ?? '')
  const [disc, setDisc] = useState<Discovery | null>(null)
  const [discMs, setDiscMs] = useState<number | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [qrLabel, setQrLabel] = useState<{ fc: string; url: string; text: string } | null>(null)
  const unbound = drones.filter((d) => !d.binding || d.binding.state === 'connection_failed')
  const runningBench = Object.entries(benches).find(([, s]) => s === 'ready' || s === 'running')?.[0] ?? null

  const run = async <T,>(label: string, fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(label)
    setError(null)
    try {
      return await fn()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      return undefined
    } finally {
      setBusy(null)
    }
  }

  const discover = () => run('discover', async () => {
    const t0 = performance.now()
    const d = await mc.discover()
    setDiscMs(Math.round(performance.now() - t0))
    setDisc(d)
  })

  useEffect(() => {
    const t = window.setTimeout(() => void discover(), 0)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- discover once on open
  }, [])

  const bind = (fc: DiscoveredFc) => run('bind', async () => {
    const b = await mc.bindFc(droneId, fc.connection_type, fc.endpoint, 'discovery', fc.system_id ?? undefined)
    if (b.state === 'bound') setDone(`${fc.fc_id ?? fc.endpoint} bound to ${drones.find((d) => d.id === droneId)?.name}`)
    else setError(`Binding ${b.state}: ${b.error ?? ''}`)
    onBound()
    await discover()
  })

  const showQr = async (fc: DiscoveredFc) => {
    const { qr_text } = await mc.qrPayload(fc)
    setQrLabel({ fc: fc.fc_id ?? fc.endpoint, text: qr_text, url: await QRCode.toDataURL(qr_text, { margin: 1, width: 220 }) })
  }

  return (
    <div className="dm-dialog-backdrop" role="dialog" aria-modal="true" aria-label="Bind flight controller" data-testid="mc-bind-dialog">
      <div className="dm-dialog">
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <h2 style={{ margin: 0, fontSize: '1.15rem', textTransform: 'uppercase', flex: 1 }}>Flight controller binding</h2>
          <button type="button" className="dm-btn dm-btn--ghost" onClick={onClose} aria-label="Close" data-testid="mc-bind-close"><X size={14} /></button>
        </div>
        <label style={{ fontSize: '.8rem' }}>Drone record
          <select className="dm-input" value={droneId} onChange={(e) => setDroneId(e.target.value)} style={{ display: 'block', marginTop: 3 }} data-testid="mc-bind-drone">
            <option value="">Select a drone…</option>
            {unbound.map((d) => <option key={d.id} value={d.id}>{d.name}{d.simulated ? ' (simulation record)' : ' (real aircraft record)'}</option>)}
          </select>
        </label>
        <div className="dm-tabs" role="tablist" style={{ margin: 0 }}>
          <button type="button" role="tab" aria-selected={tab === 'discover'} onClick={() => setTab('discover')} data-testid="mc-bind-tab-discover"><Radar size={12} /> Auto-discover</button>
          <button type="button" role="tab" aria-selected={tab === 'qr'} onClick={() => setTab('qr')} data-testid="mc-bind-tab-qr"><QrCode size={12} /> QR scan</button>
          <button type="button" role="tab" aria-selected={tab === 'manual'} onClick={() => setTab('manual')} data-testid="mc-bind-tab-manual">Manual</button>
        </div>
        {error && <div className="dm-banner dm-banner--bad" role="alert" style={{ fontSize: '.75rem', textTransform: 'none', letterSpacing: 0 }} data-testid="mc-bind-error">{error}</div>}
        {done && <div className="dm-banner dm-banner--ok" style={{ fontSize: '.75rem', textTransform: 'none', letterSpacing: 0 }} data-testid="mc-bind-done">{done}. Not armed: arming is a separate, checked step.</div>}

        {tab === 'discover' && (
          <div style={{ display: 'grid', gap: 6 }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', fontSize: '.75rem' }}>
              <button type="button" className="dm-btn dm-btn--primary" disabled={!!busy} onClick={() => void discover()} data-testid="mc-discover">{busy === 'discover' ? 'Discovering…' : 'Auto-discover'}</button>
              {discMs != null && <span className="dm-mono" data-testid="mc-discover-ms">{disc?.controllers.length ?? 0} found in {discMs} ms</span>}
              <span style={{ marginLeft: 'auto', opacity: 0.8 }}>Scope: {disc?.scope.sitl ?? '—'}{disc?.scope.hardware_allow_list.length ? ` + ${disc.scope.hardware_allow_list.length} approved hardware endpoint(s)` : ' · no hardware endpoints approved'}</span>
            </div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: '.72rem' }}>
              {runningBench ? (
                <>
                  <span>SITL bench <strong className="dm-mono">{runningBench}</strong> running.</span>
                  <button type="button" className="dm-btn dm-btn--ghost" disabled={!!busy} onClick={() => void run('bench', async () => { await mc.stopBench(runningBench); onBound(); await discover() })} data-testid="mc-bench-stop">Stop bench</button>
                </>
              ) : (
                <>
                  <span>No SITL bench running.</span>
                  <button type="button" className="dm-btn" disabled={!!busy} onClick={() => void run('bench', async () => { await mc.startBench(3); onBound(); await discover() })} data-testid="mc-bench-start">
                    {busy === 'bench' ? 'Starting Gazebo + 3 PX4 SITL… (~90 s)' : 'Start SITL bench (3 PX4)'}
                  </button>
                </>
              )}
            </div>
            {disc?.errors.map((e) => <p key={e} className="dm-error" style={{ fontSize: '.72rem' }}>{e}</p>)}
            <table className="dm-data-table" data-testid="mc-discovered"><thead><tr><th>Endpoint</th><th>Type</th><th>Sysid</th><th>Firmware</th><th>Status</th><th>Bound</th><th /></tr></thead><tbody>
              {disc?.controllers.map((fc) => (
                <tr key={fc.fc_identity} data-testid={`mc-fc-${fc.fc_id ?? fc.endpoint}`}>
                  <td className="dm-mono" style={{ fontSize: '.66rem' }}>{fc.endpoint}</td>
                  <td>{fc.simulation ? <span className="dm-src dm-src--sitl">SITL</span> : <span className="dm-src dm-src--hardware">{fc.connection_type.toUpperCase()}</span>}</td>
                  <td className="dm-mono">{fc.system_id ?? '—'}</td>
                  <td className="dm-mono" style={{ fontSize: '.66rem' }}>{fc.firmware ?? '—'}</td>
                  <td className="dm-mono">{fc.status}</td>
                  <td>{fc.bound ? fc.bound_drone_name : '—'}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {!fc.bound && <button type="button" className="dm-btn dm-btn--primary" style={{ padding: '.1rem .4rem' }} disabled={!droneId || !!busy} onClick={() => void bind(fc)} data-testid={`mc-bind-${fc.fc_id ?? fc.endpoint}`}>Bind</button>}
                    <button type="button" className="dm-btn dm-btn--ghost" style={{ padding: '.1rem .4rem' }} onClick={() => void showQr(fc)} title="Show this controller's binding QR label"><QrCode size={11} /></button>
                  </td>
                </tr>
              ))}
              {disc && !disc.controllers.length && <tr><td colSpan={7}>No controllers in scope. Start a SITL bench, or approve hardware endpoints (FC_HARDWARE_ENDPOINTS) and enter them manually.</td></tr>}
            </tbody></table>
            {qrLabel && (
              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }} data-testid="mc-qr-label">
                <img src={qrLabel.url} alt={`Binding QR for ${qrLabel.fc}`} width={140} height={140} />
                <div className="dm-mono" style={{ fontSize: '.62rem', wordBreak: 'break-all' }}>{qrLabel.text}<br /><span style={{ opacity: 0.7 }}>QR v1 payload: identity only, no secrets.</span></div>
              </div>
            )}
          </div>
        )}
        {tab === 'qr' && <QrScanTab droneId={droneId} onBound={(msg) => { setDone(msg); onBound() }} setError={setError} />}
        {tab === 'manual' && <ManualTab droneId={droneId} onBound={(msg) => { setDone(msg); onBound() }} setError={setError} />}
      </div>
    </div>
  )
}

function QrScanTab({ droneId, onBound, setError }: { droneId: string; onBound: (msg: string) => void; setError: (e: string | null) => void }) {
  const video = useRef<HTMLVideoElement>(null)
  const [stream, setStream] = useState<MediaStream | null>(null)
  const [scanned, setScanned] = useState<string | null>(null)
  const [check, setCheck] = useState<Awaited<ReturnType<typeof mc.validateQr>> | null>(null)
  const [camErr, setCamErr] = useState<string | null>(null)
  const [manualText, setManualText] = useState('')
  const supported = typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia

  useEffect(() => () => stream?.getTracks().forEach((t) => t.stop()), [stream])

  useEffect(() => {
    if (!stream || scanned) return
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    const t = window.setInterval(() => {
      const v = video.current
      if (!v || !v.videoWidth || !ctx) return
      canvas.width = v.videoWidth
      canvas.height = v.videoHeight
      ctx.drawImage(v, 0, 0)
      const code = jsQR(ctx.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height)
      if (code?.data) {
        setScanned(code.data)
        stream.getTracks().forEach((tr) => tr.stop())
        setStream(null)
      }
    }, 200)
    return () => window.clearInterval(t)
  }, [stream, scanned])

  useEffect(() => {
    if (!scanned) return
    mc.validateQr(scanned).then(setCheck).catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [scanned, setError])

  const open = async () => {
    setCamErr(null)
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: true })
      setStream(s)
      if (video.current) {
        video.current.srcObject = s
        await video.current.play()
      }
    } catch (err) {
      setCamErr(`Camera unavailable: ${err instanceof Error ? err.message : err}. Use manual entry below.`)
    }
  }

  return (
    <div style={{ display: 'grid', gap: 6, fontSize: '.78rem' }}>
      {!supported && <p>This browser has no camera API; paste the QR text below.</p>}
      {supported && !stream && !scanned && <button type="button" className="dm-btn dm-btn--primary" onClick={() => void open()} data-testid="mc-qr-camera"><Camera size={13} /> Open camera scanner</button>}
      <video ref={video} muted playsInline style={{ width: '100%', maxHeight: 220, background: '#000', display: stream ? 'block' : 'none' }} data-testid="mc-qr-video" />
      {camErr && <p className="dm-error">{camErr}</p>}
      {!scanned && (
        <label>QR text (fallback)
          <input className="dm-input" value={manualText} onChange={(e) => setManualText(e.target.value)} placeholder='DMXFC1:{"v":1,...}' style={{ display: 'block', width: '100%' }} data-testid="mc-qr-text" />
          <button type="button" className="dm-btn" style={{ marginTop: 4 }} disabled={!manualText} onClick={() => setScanned(manualText.trim())} data-testid="mc-qr-text-submit">Validate</button>
        </label>
      )}
      {scanned && (
        <div data-testid="mc-qr-result">
          <div className="dm-mono" style={{ fontSize: '.64rem', wordBreak: 'break-all' }}>Scanned: {scanned}</div>
          {check && (
            <div className="dm-card" style={{ background: 'var(--dm-slate-700)', padding: '.4rem', marginTop: 4 }}>
              <div>Controller <strong className="dm-mono" data-testid="mc-qr-fc">{check.payload.fc}</strong> · {check.payload.conn.toUpperCase()} · sysid {check.payload.sysid ?? '?'}</div>
              <div>{check.discovered ? `Discovered: ${check.discovered.firmware ?? 'firmware ?'}, ${check.discovered.status}${check.discovered.bound ? `, already bound to ${check.discovered.bound_drone_name}` : ''}` : 'Not currently discovered (not reachable in the discovery scope)'}</div>
              <button type="button" className="dm-btn dm-btn--primary" style={{ marginTop: 4 }} disabled={!check.bindable || !droneId} data-testid="mc-qr-bind" onClick={async () => {
                setError(null)
                try {
                  const b = await mc.bindQr(droneId, scanned)
                  if (b.state === 'bound') onBound(`${check.payload.fc} bound via QR`)
                  else setError(`Binding ${b.state}: ${b.error ?? ''}`)
                } catch (err) {
                  setError(err instanceof Error ? err.message : String(err))
                }
              }}>Confirm binding</button>
            </div>
          )}
          <button type="button" className="dm-btn dm-btn--ghost" style={{ marginTop: 4 }} onClick={() => { setScanned(null); setCheck(null) }}>Scan again</button>
        </div>
      )}
    </div>
  )
}

function ManualTab({ droneId, onBound, setError }: { droneId: string; onBound: (msg: string) => void; setError: (e: string | null) => void }) {
  const [conn, setConn] = useState<'sitl' | 'udp' | 'serial'>('sitl')
  const [endpoint, setEndpoint] = useState('')
  return (
    <div style={{ display: 'grid', gap: 6, fontSize: '.78rem' }}>
      <label>Connection type
        <select className="dm-input" value={conn} onChange={(e) => setConn(e.target.value as typeof conn)} style={{ display: 'block' }} data-testid="mc-manual-conn">
          <option value="sitl">SITL (session-managed bench controller)</option>
          <option value="udp">UDP (approved hardware endpoint)</option>
          <option value="serial">Serial (approved hardware device)</option>
        </select>
      </label>
      <label>Endpoint
        <input className="dm-input" value={endpoint} onChange={(e) => setEndpoint(e.target.value)} style={{ display: 'block', width: '100%' }} data-testid="mc-manual-endpoint"
          placeholder={conn === 'sitl' ? 'sitl:bench-xxxxxx:FC-xxxxxx-01' : conn === 'udp' ? 'udp:192.168.1.10:14550' : 'serial:/dev/ttyACM0'} />
      </label>
      <p style={{ margin: 0, opacity: 0.75 }}>Validated by the server: fixed formats only, hardware endpoints must be on the approved list, nothing is executed.</p>
      <button type="button" className="dm-btn dm-btn--primary" disabled={!droneId || !endpoint} data-testid="mc-manual-bind" onClick={async () => {
        setError(null)
        try {
          const b = await mc.bindFc(droneId, conn, endpoint.trim(), 'manual')
          if (b.state === 'bound') onBound(`${endpoint} bound manually`)
          else setError(`Binding ${b.state}: ${b.error ?? ''}`)
        } catch (err) {
          setError(err instanceof Error ? err.message : String(err))
        }
      }}>Bind</button>
    </div>
  )
}
