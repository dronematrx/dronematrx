import { useEffect, useState } from 'react'
import { Database, RefreshCw, Rewind, Server } from 'lucide-react'
import * as opsApi from '../api/ops'
import type { FleetWindow, MessagingStatus, ReplayResult, TelemetryStats, TelemetryWindow } from '../api/ops'
import { AppShell } from '../components/layout/AppShell'
import { LinkPill } from '../components/ops/FleetWall'
import type { LinkType } from '../api/types'

type Tab = 'telemetry' | 'bus'

function Stat({ label, value, testId }: { label: string; value: string; testId?: string }) {
  return (
    <div className="dm-stat" data-testid={testId}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function TelemetryTab() {
  const [stats, setStats] = useState<TelemetryStats | null>(null)
  const [minutes, setMinutes] = useState(10)
  const [droneId, setDroneId] = useState('')
  const [window, setWindow] = useState<TelemetryWindow | null>(null)
  const [fleet, setFleet] = useState<FleetWindow | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = async (m = minutes) => {
    try {
      setStats(await opsApi.getTelemetryStats(m))
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load telemetry stats')
    }
  }

  useEffect(() => {
    let cancelled = false
    opsApi
      .getTelemetryStats(10)
      .then((s) => !cancelled && setStats(s))
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : 'Failed'))
    return () => {
      cancelled = true
    }
  }, [])

  const runQuery = async () => {
    const id = droneId || stats?.per_drone[0]?.drone_id
    if (!id) return
    setBusy(true)
    try {
      setDroneId(id)
      const [w, f] = await Promise.all([opsApi.getDroneTelemetry(id, 60), opsApi.getFleetTelemetry(60)])
      setWindow(w)
      setFleet(f)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Query failed')
    } finally {
      setBusy(false)
    }
  }

  const ing = stats?.ingestor
  const windowSeconds = minutes * 60
  return (
    <div>
      <div style={{ display: 'flex', gap: 'var(--dm-space-sm)', alignItems: 'center', marginBottom: 'var(--dm-space-md)', flexWrap: 'wrap' }}>
        <label className="dm-field" style={{ gridAutoFlow: 'column', alignItems: 'center' }}>
          <span>Window</span>
          <select className="dm-input" value={minutes} onChange={(e) => { setMinutes(Number(e.target.value)); load(Number(e.target.value)) }} aria-label="Window minutes">
            {[1, 5, 10, 30, 60].map((m) => <option key={m} value={m}>{m} min</option>)}
          </select>
        </label>
        <button type="button" className="dm-btn" onClick={() => load()} data-testid="telemetry-refresh">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>
      {error && <p className="dm-error">{error}</p>}
      <div className="dm-stat-grid">
        <Stat label="Rows in window" value={String(stats?.total_rows ?? '--')} testId="ts-total-rows" />
        <Stat label="Drones reporting" value={String(stats?.per_drone.length ?? '--')} testId="ts-drones" />
        <Stat label="Ingested (session)" value={String(ing?.inserted ?? '--')} />
        <Stat label="Rejected · errors" value={`${ing?.rejected ?? '--'} · ${ing?.write_errors ?? '--'}`} testId="ts-errors" />
        <Stat label="Ingest latency p95" value={ing?.ingest_latency_ms_p95 != null ? `${ing.ingest_latency_ms_p95} ms` : '--'} />
        <Stat label="Stats query" value={stats ? `${stats.query_ms} ms` : '--'} />
      </div>
      <table className="dm-data-table" data-testid="ts-per-drone">
        <thead>
          <tr><th>Drone</th><th>Rows</th><th>Expected @1 Hz</th><th>First</th><th>Last</th></tr>
        </thead>
        <tbody>
          {stats?.per_drone.map((d) => {
            const span = Math.min(windowSeconds, Math.round((new Date(d.last).getTime() - new Date(d.first).getTime()) / 1000) + 1)
            return (
              <tr key={d.drone_id}>
                <td>{d.name}</td>
                <td className="dm-mono">{d.rows}</td>
                <td className="dm-mono">{span}</td>
                <td className="dm-mono">{new Date(d.first).toLocaleTimeString()}</td>
                <td className="dm-mono">{new Date(d.last).toLocaleTimeString()}</td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <div className="dm-card" style={{ marginTop: 'var(--dm-space-md)' }}>
        <div className="dm-card-title">1-Hour Query (TimescaleDB hypertable)&hellip;</div>
        <div style={{ display: 'flex', gap: 'var(--dm-space-sm)', flexWrap: 'wrap' }}>
          <select className="dm-input" value={droneId} onChange={(e) => setDroneId(e.target.value)} aria-label="Drone">
            <option value="">{stats?.per_drone[0]?.name ?? 'Drone'}…</option>
            {stats?.per_drone.map((d) => <option key={d.drone_id} value={d.drone_id}>{d.name}</option>)}
          </select>
          <button type="button" className="dm-btn dm-btn--primary" onClick={runQuery} disabled={busy} data-testid="ts-run-query">
            <Database size={14} /> Run 1-hour query
          </button>
        </div>
        {window && fleet && (
          <div className="dm-stat-grid" style={{ marginTop: 'var(--dm-space-sm)' }}>
            <Stat label="Drone rows (1 h)" value={String(window.rows)} />
            <Stat label="Drone query" value={`${window.query_ms} ms`} testId="ts-drone-query-ms" />
            <Stat label="Fleet rows (1 h)" value={String(fleet.rows)} />
            <Stat label="Fleet query" value={`${fleet.query_ms} ms`} testId="ts-fleet-query-ms" />
          </div>
        )}
        {window && (
          <table className="dm-data-table" data-testid="ts-records">
            <thead>
              <tr><th>Timestamp</th><th>Lat</th><th>Lon</th><th>Alt</th><th>Speed</th><th>Heading</th><th>SOC</th><th>RSSI</th><th>Link</th></tr>
            </thead>
            <tbody>
              {window.records.slice(-15).reverse().map((r) => (
                <tr key={r.timestamp}>
                  <td className="dm-mono">{new Date(r.timestamp).toLocaleTimeString()}</td>
                  <td className="dm-mono">{r.lat.toFixed(6)}</td>
                  <td className="dm-mono">{r.lon.toFixed(6)}</td>
                  <td className="dm-mono">{r.alt.toFixed(1)}</td>
                  <td className="dm-mono">{r.speed.toFixed(1)}</td>
                  <td className="dm-mono">{r.heading.toFixed(0)}</td>
                  <td className="dm-mono">{r.soc.toFixed(1)}</td>
                  <td className="dm-mono">{r.rssi?.toFixed(0) ?? '--'}</td>
                  <td>{r.link_type ? <LinkPill link={r.link_type as LinkType} /> : '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function BusTab() {
  const [status, setStatus] = useState<MessagingStatus | null>(null)
  const [replay, setReplay] = useState<ReplayResult | null>(null)
  const [kind, setKind] = useState('telemetry')
  const [minutes, setMinutes] = useState(5)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    try {
      setStatus(await opsApi.getMessagingStatus())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Message bus unavailable')
    }
  }
  useEffect(() => {
    let cancelled = false
    const tick = () => opsApi.getMessagingStatus().then((s) => !cancelled && setStatus(s)).catch(() => undefined)
    tick()
    const timer = setInterval(tick, 2000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  const runReplay = async () => {
    try {
      setReplay(await opsApi.replayMessages({ kind, minutes, limit: 200 }))
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Replay failed')
    }
  }

  const bus = status?.bus
  const orch = status?.orchestrator
  return (
    <div>
      {error && <p className="dm-error">{error}</p>}
      <div className="dm-stat-grid">
        <Stat label="NATS" value={bus?.connected ? 'CONNECTED' : 'DOWN'} testId="nats-connected" />
        <Stat label="Stream" value={bus?.stream ?? '--'} />
        <Stat label="Stored messages" value={String(bus?.messages ?? '--')} testId="nats-messages" />
        <Stat label="Retention" value={bus?.max_age_hours ? `${bus.max_age_hours} h (${bus.retention})` : '--'} />
        <Stat label="Delivery avg · p95" value={orch?.latency_ms_avg != null ? `${orch.latency_ms_avg.toFixed(2)} · ${orch.latency_ms_p95?.toFixed(2)} ms` : '--'} testId="nats-latency" />
        <Stat label="Max · >50 ms" value={orch?.latency_ms_max != null ? `${orch.latency_ms_max.toFixed(1)} ms · ${orch.over_50ms}` : '--'} />
        <Stat label="Publish RTT" value={bus?.publish_rtt_ms_avg != null ? `${bus.publish_rtt_ms_avg} ms` : '--'} />
        <Stat label="By kind" value={orch ? Object.entries(orch.by_kind).filter(([k]) => k !== 'replayed_backlog').map(([k, v]) => `${k[0].toUpperCase()}:${v}`).join(' ') : '--'} />
      </div>
      <div className="dm-card" style={{ marginBottom: 'var(--dm-space-md)' }}>
        <div className="dm-card-title">Subjects&hellip;</div>
        <p className="dm-mono" style={{ fontSize: '.75rem', margin: 0 }}>{bus?.topics?.join('   ')}</p>
        <p className="dm-mono" style={{ fontSize: '.7rem', opacity: 0.7, margin: '.25rem 0 0' }}>NATS: {bus?.subjects?.join('  ')}</p>
      </div>
      <table className="dm-data-table" data-testid="nats-consumers">
        <thead>
          <tr><th>Consumer group</th><th>Filters</th><th>Delivered seq</th><th>Ack floor</th><th>Pending</th><th>Ack pending</th><th>Redelivered</th></tr>
        </thead>
        <tbody>
          {bus?.consumers?.map((c) => (
            <tr key={c.name}>
              <td>{c.name}</td>
              <td className="dm-mono" style={{ fontSize: '.7rem' }}>{c.filters.map((f) => f.split('.').slice(-1)[0]).join(', ')}</td>
              <td className="dm-mono">{c.delivered}</td>
              <td className="dm-mono">{c.ack_floor}</td>
              <td className="dm-mono">{c.num_pending}</td>
              <td className="dm-mono">{c.num_ack_pending}</td>
              <td className="dm-mono">{c.num_redelivered}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 style={{ textTransform: 'uppercase', fontSize: '.9rem', letterSpacing: '.06em', margin: 'var(--dm-space-md) 0 .5rem' }}>Live message flow&hellip;</h3>
      <table className="dm-data-table" data-testid="nats-recent">
        <thead>
          <tr><th>Seq</th><th>Topic</th><th>Link</th><th>Latency</th><th>Content</th></tr>
        </thead>
        <tbody>
          {status?.recent.slice(-12).reverse().map((m) => (
            <tr key={m.stream_seq}>
              <td className="dm-mono">{m.stream_seq}</td>
              <td className="dm-mono">{m.topic}</td>
              <td>{m.link ? <LinkPill link={m.link as LinkType} /> : '--'}</td>
              <td className="dm-mono">{m.latency_ms?.toFixed(2)} ms</td>
              <td className="dm-mono" style={{ fontSize: '.7rem' }}>{m.summary}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="dm-card" style={{ marginTop: 'var(--dm-space-md)' }}>
        <div className="dm-card-title">JetStream Replay&hellip;</div>
        <div style={{ display: 'flex', gap: 'var(--dm-space-sm)', flexWrap: 'wrap' }}>
          <select className="dm-input" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Replay kind">
            {['telemetry', 'state', 'alert', 'command'].map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <select className="dm-input" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} aria-label="Replay window">
            {[1, 5, 15, 60, 1440].map((m) => <option key={m} value={m}>last {m >= 60 ? `${m / 60} h` : `${m} min`}</option>)}
          </select>
          <button type="button" className="dm-btn dm-btn--primary" onClick={runReplay} data-testid="nats-replay">
            <Rewind size={14} /> Replay from stream
          </button>
          <button type="button" className="dm-btn" onClick={load}>
            <Server size={14} /> Refresh
          </button>
        </div>
        {replay && (
          <>
            <p className="dm-mono" style={{ fontSize: '.75rem' }} data-testid="nats-replay-result">
              Replayed {replay.count} messages · seq {replay.first_seq}–{replay.last_seq} · {replay.replay_ms} ms
            </p>
            <table className="dm-data-table">
              <thead>
                <tr><th>Seq</th><th>Topic</th><th>Published</th><th>Link</th><th>Payload</th></tr>
              </thead>
              <tbody>
                {replay.messages.slice(0, 12).map((m) => (
                  <tr key={m.stream_seq}>
                    <td className="dm-mono">{m.stream_seq}</td>
                    <td className="dm-mono">{m.topic}</td>
                    <td className="dm-mono">{m.published_at ? new Date(m.published_at).toLocaleTimeString() : '--'}</td>
                    <td>{m.link ? <LinkPill link={m.link as LinkType} /> : '--'}</td>
                    <td className="dm-mono" style={{ fontSize: '.68rem' }}>
                      {JSON.stringify(m.payload).slice(0, 110)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  )
}

export function AnalyticsPage() {
  const [tab, setTab] = useState<Tab>('telemetry')
  return (
    <AppShell title="Analyse">
      <div className="dm-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'telemetry'} onClick={() => setTab('telemetry')} data-testid="tab-telemetry">
          Telemetry · TimescaleDB
        </button>
        <button type="button" role="tab" aria-selected={tab === 'bus'} onClick={() => setTab('bus')} data-testid="tab-bus">
          Message Bus · NATS
        </button>
      </div>
      {tab === 'telemetry' ? <TelemetryTab /> : <BusTab />}
    </AppShell>
  )
}
