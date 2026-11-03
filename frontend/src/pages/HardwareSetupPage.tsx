import { useCallback, useEffect, useState } from 'react'
import { Cable, Plane, Power, RefreshCw, Usb } from 'lucide-react'
import * as mc from '../api/missionControl'
import type { BridgeStatus, HwController, SerialPort } from '../api/missionControl'
import { ApiError } from '../api/client'
import { AppShell } from '../components/layout/AppShell'
import { DroneSetup } from '../components/hwsetup/DroneSetup'
import { useAuthStore } from '../store/useAuthStore'

const errText = (err: unknown) => (err instanceof ApiError ? `${err.code ? `${err.code}: ` : ''}${err.message}` : String(err))

const KIND_LABEL: Record<SerialPort['kind'], string> = {
  radio: 'Telemetry radio',
  autopilot: 'Flight controller (USB cable)',
  unknown: 'Unknown device',
}

/**
 * Real-drone setup, start to finish, without Mission Planner:
 * 1. start the hardware bridge, 2. connect the radios, 3. set up each drone.
 */
export function HardwareSetupPage() {
  const role = useAuthStore((s) => s.user?.role)
  const canWrite = role === 'ADMIN' || role === 'OPERATOR'
  const [bridge, setBridge] = useState<BridgeStatus | null>(null)
  const [ports, setPorts] = useState<SerialPort[]>([])
  const [controllers, setControllers] = useState<HwController[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [network, setNetwork] = useState('')

  const refresh = useCallback(async () => {
    try {
      const b = await mc.bridgeStatus()
      setBridge(b)
      if (b.running) {
        const [c, p] = await Promise.all([mc.hwControllers(), canWrite ? mc.serialPorts() : Promise.resolve({ ports: [] })])
        setControllers(c.controllers)
        setPorts(p.ports)
      } else {
        setControllers([])
      }
    } catch (err) {
      setError(errText(err))
    }
  }, [canWrite])

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- poll the bridge (external system)
    void refresh()
    const t = window.setInterval(() => void refresh(), 3000)
    return () => window.clearInterval(t)
  }, [refresh])

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label)
    setError(null)
    try {
      await fn()
      await refresh()
    } catch (err) {
      setError(errText(err))
    } finally {
      setBusy(null)
    }
  }

  const links = bridge?.health?.links ?? []
  const linkFor = (device: string) => links.find((l) => l.endpoint === `serial:${device}`)
  const networkLinks = links.filter((l) => !l.endpoint.startsWith('serial:'))
  const current = controllers.find((c) => c.fc_id === selected) ?? null
  const dupIds = new Set(controllers.map((c) => c.system_id).filter((id, i, a) => a.indexOf(id) !== i))

  return (
    <AppShell title="Drone Setup">
      <div style={{ display: 'grid', gap: '1rem', maxWidth: 1200, width: '100%', margin: '0 auto' }} data-testid="hw-setup-page">
        <div className="dm-banner" style={{ fontSize: '.85rem', textTransform: 'none', letterSpacing: 0 }}>
          Set up real drones here, start to finish: no Mission Planner needed. Work through the three sections in order.
          Keep the <strong>propellers OFF</strong> for the whole setup.
        </div>
        {error && <div className="dm-banner dm-banner--bad" style={{ fontSize: '.85rem', textTransform: 'none', letterSpacing: 0 }} data-testid="hw-setup-error">{error}</div>}

        {/* 1. bridge */}
        <section className="dm-card" style={{ padding: '1rem', display: 'grid', gap: '.6rem' }} data-testid="hw-setup-bridge">
          <div className="dm-card-title" style={{ margin: 0 }}><Power size={14} /> 1. Start the drone connection service</div>
          <div style={{ fontSize: '.85rem', opacity: 0.85 }}>
            The connection service talks to the drones through the radios plugged into this laptop. Close Mission Planner or any other ground
            station first: only one program can use a radio at a time.
          </div>
          {bridge === null ? 'Checking...' : bridge.running ? (
            <div style={{ display: 'flex', gap: '.8rem', alignItems: 'center', flexWrap: 'wrap' }}>
              <span className="dm-banner dm-banner--ok" style={{ fontSize: '.8rem' }} data-testid="hw-bridge-running">RUNNING</span>
              <span style={{ fontSize: '.8rem' }}>
                {bridge.health?.nats?.connected ? 'Connected to Drone MatrX telemetry.' : 'Telemetry service (NATS) not connected yet: start Docker.'}
              </span>
              {bridge.started_here && canWrite && (
                <button type="button" className="dm-btn dm-btn--ghost" disabled={!!busy} onClick={() => run('stop', mc.stopBridge)}>Stop service</button>
              )}
            </div>
          ) : (
            <div style={{ display: 'grid', gap: '.5rem' }}>
              <div style={{ display: 'flex', gap: '.8rem', alignItems: 'center' }}>
                <span className="dm-banner dm-banner--bad" style={{ fontSize: '.8rem' }}>NOT RUNNING</span>
                {bridge.can_start && canWrite && (
                  <button type="button" className="dm-btn dm-btn--primary" disabled={!!busy} data-testid="hw-bridge-start" onClick={() => run('start', mc.startBridge)}>
                    {busy === 'start' ? 'Starting...' : 'Start service'}
                  </button>
                )}
              </div>
              {!bridge.can_start && <div style={{ fontSize: '.8rem' }}>Start it on the laptop the radios are plugged into: <code>python -m hwbridge.server</code></div>}
              {bridge.log_tail.length > 0 && (
                <details><summary style={{ fontSize: '.8rem', cursor: 'pointer' }}>Service log</summary>
                  <pre style={{ fontSize: '.7rem', maxHeight: 180, overflow: 'auto', whiteSpace: 'pre-wrap' }}>{bridge.log_tail.join('\n')}</pre>
                </details>
              )}
            </div>
          )}
        </section>

        {/* 2. connections */}
        {bridge?.running && (
          <section className="dm-card" style={{ padding: '1rem', display: 'grid', gap: '.6rem' }} data-testid="hw-setup-ports">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div className="dm-card-title" style={{ margin: 0 }}><Usb size={14} /> 2. Connect the radios</div>
              <button type="button" className="dm-btn dm-btn--ghost" onClick={() => void refresh()} title="Look for new devices"><RefreshCw size={14} /></button>
            </div>
            <ol style={{ margin: 0, paddingLeft: '1.2rem', fontSize: '.85rem', display: 'grid', gap: '.2rem' }}>
              <li>Plug every ground radio into the laptop (a USB hub is fine). Each radio shows up below as its own COM port.</li>
              <li>Power on the drones and the RC transmitters.</li>
              <li>Press <strong>Connect</strong> on each radio. Drones on it appear in section 3 within a few seconds (speed is detected automatically).</li>
            </ol>
            {ports.length === 0 && <div style={{ fontSize: '.85rem' }}>No USB serial devices found. Plug in a radio and press refresh. If it still does not appear, install its driver (CP210x or FTDI).</div>}
            <div style={{ display: 'grid', gap: '.35rem' }}>
              {ports.filter((p) => p.kind !== 'unknown' || p.connected || !/bluetooth/i.test(p.description)).map((p) => {
                const link = linkFor(p.device)
                return (
                  <div key={p.device} style={{ display: 'flex', gap: '.7rem', alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid rgb(255 255 255 / .08)', paddingBottom: '.35rem' }}
                    data-testid={`hw-port-${p.device}`}>
                    <strong style={{ width: 70 }}>{p.device}</strong>
                    <span style={{ flex: 1, fontSize: '.8rem' }}>{KIND_LABEL[p.kind]} &middot; {p.description}</span>
                    {link && (
                      <span style={{ fontSize: '.8rem' }}>
                        {link.error ? <span style={{ color: '#FF8A80' }}>{/denied|busy|PermissionError/i.test(link.error) ? 'In use by another program (close Mission Planner)' : link.error}</span>
                          : link.vehicles.length ? `${link.vehicles.length} drone(s) found at ${link.baud} baud` : `listening (${link.baud} baud)...`}
                      </span>
                    )}
                    {canWrite && (link
                      ? <button type="button" className="dm-btn dm-btn--ghost" disabled={!!busy} onClick={() => run(p.device, () => mc.disconnectLink(link.name))}>Disconnect</button>
                      : <button type="button" className="dm-btn dm-btn--primary" disabled={!!busy} data-testid={`hw-connect-${p.device}`}
                        onClick={() => run(p.device, () => mc.connectLink(`serial:${p.device}`, 0))}>{busy === p.device ? 'Connecting...' : 'Connect'}</button>)}
                  </div>
                )
              })}
            </div>
            <details>
              <summary style={{ fontSize: '.8rem', cursor: 'pointer' }}><Cable size={12} /> Network connection (Wi-Fi telemetry, companion computer, simulator)</summary>
              <div style={{ display: 'grid', gap: '.4rem', marginTop: '.4rem' }}>
                {networkLinks.map((l) => (
                  <div key={l.endpoint} style={{ display: 'flex', gap: '.7rem', fontSize: '.8rem', alignItems: 'center' }}>
                    <code>{l.endpoint}</code><span>{l.vehicles.length} drone(s)</span>
                    {canWrite && <button type="button" className="dm-btn dm-btn--ghost" onClick={() => run(l.name, () => mc.disconnectLink(l.name))}>Disconnect</button>}
                  </div>
                ))}
                {canWrite && (
                  <div style={{ display: 'flex', gap: '.5rem' }}>
                    <input className="dm-input" placeholder="udp:0.0.0.0:14550 or tcp:127.0.0.1:5760" value={network} onChange={(e) => setNetwork(e.target.value)} data-testid="hw-network-endpoint" />
                    <button type="button" className="dm-btn" disabled={!network.trim() || !!busy} data-testid="hw-network-connect"
                      onClick={() => run('net', async () => { await mc.connectLink(network.trim(), 0); setNetwork('') })}>Connect</button>
                  </div>
                )}
              </div>
            </details>
          </section>
        )}

        {/* 3. drones */}
        {bridge?.running && (
          <section style={{ display: 'grid', gap: '.6rem' }} data-testid="hw-setup-drones">
            <div className="dm-card" style={{ padding: '1rem', display: 'grid', gap: '.5rem' }}>
              <div className="dm-card-title" style={{ margin: 0 }}><Plane size={14} /> 3. Set up each drone</div>
              {controllers.length === 0
                ? <div style={{ fontSize: '.85rem' }}>No drones found yet. Connect a radio above and make sure the drone is powered on.</div>
                : <div style={{ fontSize: '.85rem' }}>Choose a drone. Follow its steps from top to bottom; each step shows a green tick when the drone confirms it is done.</div>}
              {dupIds.size > 0 && (
                <div className="dm-banner dm-banner--amber" style={{ fontSize: '.8rem', textTransform: 'none', letterSpacing: 0 }}>
                  Several drones use the same ID ({[...dupIds].join(', ')}). Fix this first in step 1 "Unique drone ID" of each drone.
                </div>
              )}
              <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap' }}>
                {controllers.map((c) => (
                  <button key={c.fc_id} type="button" onClick={() => setSelected(c.fc_id)} data-testid={`hw-drone-${c.fc_id}`}
                    className={`dm-btn ${selected === c.fc_id ? 'dm-btn--primary' : ''}`} style={{ display: 'grid', textAlign: 'left', gap: 2 }}>
                    <span>Drone ID {c.system_id}{dupIds.has(c.system_id) ? ' (duplicate!)' : ''}</span>
                    <span style={{ fontSize: '.7rem', opacity: 0.8 }}>
                      on {c.endpoint.replace(/^serial:/, '')} &middot; {c.mode}{c.armed ? ' · ARMED' : ''}{c.bound ? ` · linked to ${c.uin}` : ''}
                      {(c.heartbeat_age_s ?? 99) > 5 ? ' · not responding' : ''}
                    </span>
                  </button>
                ))}
              </div>
            </div>
            {current && <DroneSetup key={current.fc_id} controller={current} canWrite={canWrite} onFcIdChange={(id) => setSelected(id)} />}
          </section>
        )}
      </div>
    </AppShell>
  )
}
