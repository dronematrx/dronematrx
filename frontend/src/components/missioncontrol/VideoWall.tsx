import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Circle, Film, Maximize2, PictureInPicture2, RotateCcw } from 'lucide-react'
import * as mc from '../../api/missionControl'
import type { RecordingSegment, VideoFeed } from '../../api/missionControl'
import { readVideoBarcode } from '../../lib/videoBarcode'
import { WhepPlayer, type FeedSnapshot, type Rendition } from '../../lib/whep'

export type Layout = 1 | 4 | 9

/** Glass-to-glass samples for test patterns: capture time (barcode, simhost
 * clock corrected by the measured offset) -> frame presented in this browser. */
interface LatencySample { feed: string; latencyMs: number; captureMs: number; presentedMs: number; counter: number }
declare global {
  interface Window { __dmxVideoLatency?: LatencySample[]; __dmxVideoState?: Record<string, FeedSnapshot> }
}
let clockOffset: Promise<number> | null = null
const offsetMs = () => (clockOffset ??= mc.getClockOffset().then((o) => o.offset_ms).catch(() => 0))

interface Props {
  feeds: VideoFeed[]
  relay: 'ok' | 'unavailable'
  cameraConfigured: boolean
  selectedDroneId: string | null
  canWrite: boolean
  onSelectDrone: (id: string) => void
  onRefresh: () => void
}

function defaultOrder(feeds: VideoFeed[], selected: string | null): string[] {
  const rank = (f: VideoFeed) => (f.drone_id && f.drone_id === selected ? 0 : f.kind === 'sitl_camera' && f.drone_id ? 1 : f.kind === 'hardware_camera' ? 1 : f.kind === 'sitl_camera' ? 3 : 2)
  return [...feeds].sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label)).map((f) => f.id)
}

export function VideoWall({ feeds, relay, cameraConfigured, selectedDroneId, canWrite, onSelectDrone, onRefresh }: Props) {
  const [layout, setLayout] = useState<Layout>(4)
  const [overrides, setOverrides] = useState<Record<number, string>>({})
  const [dvrFeed, setDvrFeed] = useState<string | null>(null)
  const byId = useMemo(() => new Map(feeds.map((f) => [f.id, f])), [feeds])

  // Slots hold real sources only (operator overrides first, then the default
  // order); a slot with no source left stays explicitly empty.
  const slots = useMemo(() => {
    const order = defaultOrder(feeds, selectedDroneId)
    const next: (string | null)[] = Array.from({ length: layout }, () => null)
    const used = new Set<string>()
    for (let i = 0; i < layout; i++) {
      const keep = overrides[i]
      if (keep && byId.has(keep) && !used.has(keep)) {
        next[i] = keep
        used.add(keep)
      }
    }
    for (let i = 0; i < layout; i++) {
      if (next[i]) continue
      const pick = order.find((id) => !used.has(id))
      if (pick) {
        next[i] = pick
        used.add(pick)
      }
    }
    return next
  }, [feeds, layout, byId, selectedDroneId, overrides])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minHeight: 0, flex: 1 }} data-testid="mc-video-wall">
      <div style={{ display: 'flex', gap: 4, alignItems: 'center', flexWrap: 'wrap', fontSize: '.68rem' }}>
        {([1, 4, 9] as Layout[]).map((n) => (
          <button key={n} type="button" className="dm-btn dm-btn--ghost" aria-pressed={layout === n} onClick={() => setLayout(n)} data-testid={`mc-layout-${n}`}
            style={{ padding: '.15rem .5rem', outline: layout === n ? '1px solid #FFD08A' : undefined }}>{n === 1 ? '1' : n === 4 ? '2×2' : '3×3'}</button>
        ))}
        <span className="dm-mono" style={{ opacity: 0.75 }} data-testid="mc-feed-count">{feeds.filter((f) => f.status === 'live').length} live / {feeds.length} configured</span>
        <button type="button" className="dm-btn dm-btn--ghost" style={{ marginLeft: 'auto', padding: '.15rem .45rem' }} onClick={onRefresh}>Refresh</button>
      </div>
      {relay === 'unavailable' && <div className="dm-banner dm-banner--bad" style={{ fontSize: '.7rem' }} data-testid="mc-relay-down">Media relay unreachable: no video can be shown</div>}
      {!cameraConfigured && <div className="dm-banner dm-banner--amber" style={{ fontSize: '.68rem', textTransform: 'none', letterSpacing: 0 }} data-testid="mc-no-camera">Camera source not configured. Only test patterns are available.</div>}
      <div className="dm-video-grid" data-layout={layout}>
        {slots.map((id, i) => {
          const feed = id ? byId.get(id) : undefined
          return feed ? (
            <VideoTile key={`${i}:${feed.id}`} feed={feed} selected={!!feed.drone_id && feed.drone_id === selectedDroneId} canWrite={canWrite}
              others={feeds} onAssign={(fid) => setOverrides((o) => ({ ...o, [i]: fid }))}
              onSelectDrone={onSelectDrone} onDvr={() => setDvrFeed(feed.id)} onRefresh={onRefresh} />
          ) : (
            <div key={`${i}:empty`} className="dm-video-tile" data-testid="mc-tile-empty">
              <div className="dm-video-overlay"><span>No video source for this slot<br /><span style={{ opacity: 0.7 }}>({feeds.length} source{feeds.length === 1 ? '' : 's'} configured)</span></span></div>
            </div>
          )
        })}
      </div>
      {dvrFeed && byId.get(dvrFeed) && <DvrBar feed={byId.get(dvrFeed)!} canWrite={canWrite} onClose={() => setDvrFeed(null)} onRefresh={onRefresh} />}
    </div>
  )
}

interface TileProps {
  feed: VideoFeed
  selected: boolean
  canWrite: boolean
  others: VideoFeed[]
  onAssign: (feedId: string) => void
  onSelectDrone: (id: string) => void
  onDvr: () => void
  onRefresh: () => void
}

function VideoTile({ feed, selected, canWrite, others, onAssign, onSelectDrone, onDvr, onRefresh }: TileProps) {
  const tile = useRef<HTMLDivElement>(null)
  const video = useRef<HTMLVideoElement>(null)
  const player = useRef<WhepPlayer | null>(null)
  const [snap, setSnap] = useState<FeedSnapshot | null>(null)
  // A feed with a single stream (real aircraft cameras) has no low rendition to fall back to.
  const singleStream = !feed.low_path
  const [mode, setMode] = useState<'auto' | Rendition>(singleStream ? 'hi' : 'auto')
  const pipSupported = typeof document !== 'undefined' && 'pictureInPictureEnabled' in document && document.pictureInPictureEnabled

  useEffect(() => {
    if (!video.current) return
    const p = new WhepPlayer(feed.id, video.current, (s) => {
      setSnap(s)
      ;(window.__dmxVideoState ??= {})[feed.id] = s
    })
    player.current = p
    if (singleStream) p.setMode('hi')
    p.start()
    return () => {
      p.stop() // closes the WHEP session on the relay
      player.current = null
    }
  }, [feed.id, singleStream])

  // Latency sampling on test patterns (barcode), ~2 per second.
  useEffect(() => {
    const v = video.current
    if (!v || feed.kind !== 'test_pattern' || !('requestVideoFrameCallback' in v)) return
    let handle = 0
    let last = 0
    let alive = true
    const cb = (_now: number, meta: VideoFrameCallbackMetadata) => {
      if (!alive) return
      const presented = performance.timeOrigin + (meta.expectedDisplayTime ?? meta.presentationTime)
      if (presented - last > 500) {
        last = presented
        const code = readVideoBarcode(v)
        if (code) {
          void offsetMs().then((off) => {
            const s = (window.__dmxVideoLatency ??= [])
            s.push({ feed: feed.path, latencyMs: presented - (code.captureMs - off), captureMs: code.captureMs - off, presentedMs: presented, counter: code.counter })
            if (s.length > 2000) s.shift()
          })
        }
      }
      handle = v.requestVideoFrameCallback(cb)
    }
    handle = v.requestVideoFrameCallback(cb)
    return () => {
      alive = false
      v.cancelVideoFrameCallback(handle)
    }
  }, [feed.id, feed.kind, feed.path])

  const lost = snap && (snap.state === 'reconnecting' || snap.state === 'failed' || snap.state === 'disconnected')
  const st = snap?.stats
  const fullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen()
    else void tile.current?.requestFullscreen().catch(() => undefined)
  }
  const pip = async () => {
    if (!video.current) return
    if (document.pictureInPictureElement) await document.exitPictureInPicture()
    else await video.current.requestPictureInPicture().catch(() => undefined)
  }

  return (
    <div ref={tile} className="dm-video-tile" data-selected={selected} data-testid={`mc-tile-${feed.path}`} data-feed-id={feed.id} data-state={snap?.state ?? 'idle'}
      data-failure={snap?.failure ?? ''} onDoubleClick={fullscreen}>
      <video ref={video} muted playsInline autoPlay onClick={() => feed.drone_id && onSelectDrone(feed.drone_id)} />
      <div className="dm-video-label">
        <span style={{ color: feed.kind === 'test_pattern' ? '#FFD08A' : '#C9A15A' }}>{feed.live_label}</span>
        <span>{feed.drone_name ?? feed.label}</span>
        {feed.recording && <span style={{ color: '#FF8A80' }}>● REC</span>}
        <span data-testid="tile-state">{(snap?.state ?? 'idle').toUpperCase()}{snap?.rendition === 'lo' ? ' · LOW' : ''}</span>
      </div>
      {st && !lost && (
        <div className="dm-video-stats" data-testid="tile-stats">
          {st.width ?? '?'}×{st.height ?? '?'} · {st.fps?.toFixed(0) ?? '?'} fps · {st.bitrateKbps ?? '?'} kb/s · RTT {st.rttMs?.toFixed(0) ?? '?'} ms · loss {st.lossPct?.toFixed(1) ?? '?'}% · jitter {st.jitterMs?.toFixed(0) ?? '?'} ms
        </div>
      )}
      {lost && (
        <div className={`dm-video-overlay ${snap?.failure ? 'dm-video-overlay--loss' : ''}`} data-testid="tile-link-loss">
          <span>
            <strong>{snap?.failure === 'source' ? 'SOURCE OFFLINE' : snap?.failure === 'signalling' ? 'SIGNALLING FAILED' : 'VIDEO LINK LOST'}</strong><br />
            {snap?.message}<br />
            {snap?.state === 'failed' ? (
              <button type="button" className="dm-btn" style={{ marginTop: 6 }} onClick={() => { player.current?.retry(); onRefresh() }} data-testid="tile-retry"><RotateCcw size={11} /> Retry</button>
            ) : (
              <span className="dm-mono" data-testid="tile-retry-in">reconnect attempt {snap?.attempt}/6{snap?.nextRetryS ? ` in ${snap.nextRetryS} s` : '…'}</span>
            )}
          </span>
        </div>
      )}
      {!snap || snap.state === 'connecting' ? <div className="dm-video-overlay"><span>Connecting…</span></div> : null}
      <div className="dm-video-tools">
        <select value="" onChange={(e) => e.target.value && onAssign(e.target.value)} aria-label="Change feed" style={{ background: 'rgb(0 0 0 / .65)', color: '#fff', border: 0, fontSize: '.6rem', maxWidth: 90 }}>
          <option value="">Feed…</option>
          {others.map((f) => <option key={f.id} value={f.id}>{f.drone_name ?? f.label}</option>)}
        </select>
        <button type="button" disabled={singleStream} onClick={() => { const m = mode === 'auto' ? 'lo' : mode === 'lo' ? 'hi' : 'auto'; setMode(m); player.current?.setMode(m) }}
          title={singleStream ? 'Single stream (camera has no low-quality rendition)' : 'Rendition: auto / low / high'} data-testid="tile-rendition">{mode.toUpperCase()}</button>
        <button type="button" onClick={onDvr} title="DVR / recordings" data-testid="tile-dvr"><Film size={11} /></button>
        <button type="button" onClick={() => void pip()} disabled={!pipSupported} title={pipSupported ? 'Picture-in-Picture' : 'Picture-in-Picture is not supported by this browser'} data-testid="tile-pip"><PictureInPicture2 size={11} /></button>
        <button type="button" onClick={fullscreen} title="Fullscreen" data-testid="tile-fullscreen"><Maximize2 size={11} /></button>
        {canWrite && (
          <button type="button" title={feed.recording ? 'Stop recording' : 'Start recording'} data-testid="tile-record"
            onClick={() => void mc.setRecording(feed.id, !feed.recording).then(onRefresh)}><Circle size={10} fill={feed.recording ? '#FF5A5A' : 'none'} /></button>
        )}
      </div>
    </div>
  )
}

function DvrBar({ feed, canWrite, onClose, onRefresh }: { feed: VideoFeed; canWrite: boolean; onClose: () => void; onRefresh: () => void }) {
  const video = useRef<HTMLVideoElement>(null)
  const [segments, setSegments] = useState<RecordingSegment[] | null>(null)
  const [clip, setClip] = useState<{ url: string; startMs: number; duration: number } | null>(null)
  const [pos, setPos] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => mc.getRecordings(feed.id)
    .then((r) => { setSegments(r.segments); setError(null) })
    .catch((err) => setError(err instanceof Error ? err.message : String(err))), [feed.id])
  useEffect(() => { void load() }, [load])
  useEffect(() => () => { if (clip) URL.revokeObjectURL(clip.url) }, [clip])

  const range = useMemo(() => {
    if (!segments?.length) return null
    const start = Math.min(...segments.map((s) => Date.parse(s.start)))
    const end = Math.max(...segments.map((s) => Date.parse(s.start) + s.duration * 1000))
    return { start, end }
  }, [segments])

  const openWindow = async (startMs: number) => {
    if (!range) return
    const s = Math.max(range.start, Math.min(startMs, range.end - 5000))
    const duration = Math.max(2, Math.min(60, (range.end - s) / 1000))
    setError(null)
    try {
      const url = await mc.fetchPlayback(feed.id, new Date(s).toISOString(), Math.floor(duration))
      setClip({ url, startMs: s, duration })
      setPos(0)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  /** Seek anywhere in the recording: inside the loaded clip move the playhead, otherwise load the clip that starts there. */
  const seekAbs = async (absMs: number) => {
    if (clip && absMs >= clip.startMs && absMs <= clip.startMs + clip.duration * 1000 - 500) {
      if (video.current) video.current.currentTime = (absMs - clip.startMs) / 1000
    } else await openWindow(absMs)
  }

  const abs = clip ? clip.startMs + pos * 1000 : null
  return (
    <div className="dm-card" style={{ background: 'var(--dm-slate-700)', padding: '.45rem', display: 'grid', gap: 4 }} data-testid="mc-dvr">
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '.7rem' }}>
        <strong>DVR · {feed.drone_name ?? feed.label}</strong>
        <span className="dm-mono" style={{ opacity: 0.75 }}>{feed.path}</span>
        {canWrite && (
          <button type="button" className="dm-btn dm-btn--ghost" style={{ padding: '.1rem .4rem' }} data-testid="dvr-record"
            onClick={() => void mc.setRecording(feed.id, !feed.recording).then(() => { onRefresh(); void load() })}>{feed.recording ? 'Stop recording' : 'Start recording'}</button>
        )}
        <button type="button" className="dm-btn dm-btn--ghost" style={{ marginLeft: 'auto', padding: '.1rem .4rem' }} onClick={onClose}>Close</button>
      </div>
      {error && <p className="dm-error" style={{ fontSize: '.68rem' }} data-testid="dvr-error">{error}</p>}
      {segments && !segments.length && <p style={{ fontSize: '.7rem' }} data-testid="dvr-unavailable">DVR unavailable: no recording exists for this feed{feed.recording ? ' yet (recording started, first segment pending)' : '. Start recording first'}.</p>}
      {range && (
        <>
          <div className="dm-mono" style={{ fontSize: '.64rem' }} data-testid="dvr-range">Recorded {new Date(range.start).toLocaleTimeString()} – {new Date(range.end).toLocaleTimeString()} ({((range.end - range.start) / 1000).toFixed(0)} s, {segments!.length} segment{segments!.length === 1 ? '' : 's'})</div>
          <input type="range" min={range.start} max={range.end} step={500} value={abs ?? range.start} aria-label="Recording timeline" data-testid="dvr-timeline"
            onChange={(e) => void seekAbs(Number(e.target.value))} />
          {!clip && <button type="button" className="dm-btn" onClick={() => void openWindow(range.end - 30_000)} data-testid="dvr-load">Load last 30 s</button>}
        </>
      )}
      {clip && (
        <>
          <video ref={video} src={clip.url} muted playsInline style={{ width: '100%', maxHeight: 200, background: '#000' }} data-testid="dvr-video"
            onTimeUpdate={(e) => setPos(e.currentTarget.currentTime)} onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} />
          <div style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: '.68rem' }}>
            <button type="button" className="dm-btn" onClick={() => (playing ? video.current?.pause() : void video.current?.play())} data-testid="dvr-play">{playing ? 'Pause' : 'Play'}</button>
            <input type="range" min={0} max={clip.duration} step={0.1} value={pos} style={{ flex: 1 }} aria-label="Clip position" data-testid="dvr-scrub"
              onChange={(e) => { if (video.current) video.current.currentTime = Number(e.target.value); setPos(Number(e.target.value)) }} />
            <span className="dm-mono" data-testid="dvr-time">{abs ? new Date(abs).toLocaleTimeString() : '--'} (+{pos.toFixed(1)} s)</span>
          </div>
        </>
      )}
    </div>
  )
}
