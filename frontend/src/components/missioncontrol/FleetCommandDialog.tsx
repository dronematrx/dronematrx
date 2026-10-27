import { useEffect, useState } from 'react'
import * as mc from '../../api/missionControl'
import type { FleetCommandName, FleetPreview, FleetResult } from '../../api/missionControl'
import { ApiError } from '../../api/client'
import { useMissionControlStore } from '../../store/useMissionControlStore'
import { CommandStateTag } from './DroneControl'
import { SOURCE_LABEL } from './liveFleet'

const TITLE: Record<FleetCommandName, string> = { HOLD_ALL: 'Hold all', RTL_ALL: 'Return all to launch', LAND_ALL: 'Emergency land all' }

export function FleetCommandDialog({ command, onClose }: { command: FleetCommandName; onClose: () => void }) {
  const commands = useMissionControlStore((s) => s.commands)
  const trackCommand = useMissionControlStore((s) => s.trackCommand)
  const [preview, setPreview] = useState<FleetPreview | null>(null)
  const [result, setResult] = useState<FleetResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [key] = useState(() => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}`))

  useEffect(() => {
    mc.previewFleetCommand(command).then(setPreview).catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [command])

  // Authoritative per-drone results: re-read from the backend every 2 s while any
  // is still in flight (a missed WebSocket push must not leave a stale state here).
  const fleetId = result?.fleet_command_id
  const inFlight = !!result && result.results.some((r) => r.id && ['requested', 'dispatched', 'accepted'].includes((commands[r.id] ?? r).state))
  useEffect(() => {
    if (!fleetId || !inFlight) return
    const t = window.setInterval(() => {
      void mc.listCommands({ fleetCommandId: fleetId }).then((rows) => rows.forEach(trackCommand)).catch(() => undefined)
    }, 2000)
    return () => window.clearInterval(t)
  }, [fleetId, inFlight, trackCommand])

  const execute = async () => {
    if (!preview || sending) return // double-submission guard (the backend also dedupes on the idempotency key)
    setSending(true)
    setError(null)
    try {
      const modes = Object.fromEntries(preview.targets.map((t) => [t.drone_id, t.telemetry_source]))
      const r = await mc.sendFleetCommand(command, preview.targets.map((t) => t.drone_id), preview.requires_text ? text : null, modes, key)
      r.results.forEach((c) => c.id && trackCommand(c))
      setResult(r)
    } catch (err) {
      const api = err instanceof ApiError ? err : null
      if (api?.code === 'TARGETS_CHANGED' && api.body?.preview) setPreview(api.body.preview as FleetPreview)
      setError(api ? `${api.code ?? api.status}: ${api.message}` : String(err))
    } finally {
      setSending(false)
    }
  }

  const live = (id: string | undefined) => (id ? commands[id] : undefined)

  return (
    <div className="dm-dialog-backdrop" role="dialog" aria-modal="true" aria-label={TITLE[command]} data-testid="mc-fleet-dialog">
      <div className="dm-dialog">
        <h2 style={{ margin: 0, fontSize: '1.15rem', textTransform: 'uppercase' }}>{TITLE[command]}</h2>
        {error && <div className="dm-banner dm-banner--bad" style={{ fontSize: '.75rem', textTransform: 'none', letterSpacing: 0 }} data-testid="mc-fleet-error">{error}</div>}
        {!preview && !error && <p>Checking which drones can receive {command}…</p>}
        {preview && !result && (
          <>
            <div>
              <div className="dm-card-title" style={{ fontSize: '.75rem' }}>Will receive {preview.command} ({preview.targets.length})</div>
              <table className="dm-data-table" data-testid="mc-fleet-targets"><tbody>
                {preview.targets.map((t) => <tr key={t.drone_id}><td>{t.name}</td><td><span className={`dm-src dm-src--${t.telemetry_source}`}>{SOURCE_LABEL[t.telemetry_source]}</span></td><td className="dm-mono">{t.connection_state}</td></tr>)}
                {!preview.targets.length && <tr><td>No drone can receive this command.</td></tr>}
              </tbody></table>
            </div>
            {preview.excluded.length > 0 && (
              <div>
                <div className="dm-card-title" style={{ fontSize: '.75rem' }}>Not affected ({preview.excluded.length})</div>
                <table className="dm-data-table" data-testid="mc-fleet-excluded" style={{ opacity: 0.8 }}><tbody>
                  {preview.excluded.map((t) => <tr key={t.drone_id}><td>{t.name}</td><td className="dm-mono">{t.connection_state}</td><td>{t.reason}</td></tr>)}
                </tbody></table>
              </div>
            )}
            {command === 'LAND_ALL' && (
              <p style={{ fontSize: '.78rem', margin: 0 }}>Every affected drone lands where it is. This is not always the safest action (terrain, people, water below): prefer RTL ALL unless landing in place is required. Flight-controller failsafes stay active.</p>
            )}
            {preview.requires_text && (
              <label style={{ fontSize: '.8rem' }}>Type <strong className="dm-mono">{preview.requires_text}</strong> to confirm
                <input className="dm-input" value={text} onChange={(e) => setText(e.target.value)} style={{ display: 'block', marginTop: 4 }} data-testid="mc-fleet-text" />
              </label>
            )}
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button type="button" className="dm-btn dm-btn--ghost" onClick={onClose} data-testid="mc-fleet-cancel">Cancel</button>
              <button type="button" className={`dm-btn ${command === 'LAND_ALL' ? 'dm-btn--danger' : 'dm-btn--primary'}`} data-testid="mc-fleet-confirm"
                disabled={sending || !preview.targets.length || (!!preview.requires_text && text !== preview.requires_text)} onClick={() => void execute()}>
                {sending ? 'Sending…' : `${TITLE[command]} (${preview.targets.length})`}
              </button>
            </div>
          </>
        )}
        {result && (
          <>
            <table className="dm-data-table" data-testid="mc-fleet-results"><thead><tr><th>Drone</th><th>Result</th><th>Detail</th></tr></thead><tbody>
              {result.results.map((r, i) => {
                const c = live(r.id) ?? r
                return (
                  <tr key={r.id ?? i} data-testid={`mc-fleet-result-${c.drone_name ?? r.drone_id}`}>
                    <td>{c.drone_name ?? r.drone_id}</td>
                    <td>{c.state ? <CommandStateTag c={c} /> : '—'}</td>
                    <td style={{ fontSize: '.7rem' }}>{c.failure_reason ?? (c.state === 'confirmed' ? 'confirmed by telemetry' : c.state === 'accepted' ? 'acknowledged by PX4; awaiting telemetry' : c.state === 'dispatched' ? 'on the command bus; awaiting telemetry' : '')}</td>
                  </tr>
                )
              })}
            </tbody></table>
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}><button type="button" className="dm-btn" onClick={onClose} data-testid="mc-fleet-close">Close</button></div>
          </>
        )}
      </div>
    </div>
  )
}
