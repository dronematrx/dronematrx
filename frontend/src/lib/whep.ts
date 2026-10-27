/** WebRTC (WHEP) player for one video-wall feed.
 *
 * Signalling goes through the backend (POST /video/feeds/{id}/whep, which
 * carries the operator's auth); media flows directly from the relay.
 *
 * States: connecting -> connected <-> degraded; any failure -> reconnecting
 * (back-off 1, 2, 4, 8, 8, 8 s) -> failed after the last attempt. Failures are
 * classified so the UI can say *what* is wrong:
 *   signalling  backend / media relay unreachable (no answer to the offer)
 *   source      relay reachable, nothing publishing this feed
 *   media       session negotiated but frames stopped (decoded-frame stall)
 *
 * Quality uses real getStats() values. The client steps down to the `lo`
 * rendition after 3 consecutive bad samples (loss > 5 %, RTT > 400 ms or fps
 * below half nominal) and back up after 10 good ones. */
import * as mc from '../api/missionControl'
import { ApiError } from '../api/client'

export type FeedState = 'idle' | 'connecting' | 'connected' | 'degraded' | 'reconnecting' | 'disconnected' | 'failed'
export type FailureKind = 'signalling' | 'source' | 'media' | null
export type Rendition = 'hi' | 'lo'

export interface FeedStats {
  fps: number | null
  width: number | null
  height: number | null
  bitrateKbps: number | null
  rttMs: number | null
  jitterMs: number | null
  lossPct: number | null
  framesDropped: number | null
  framesDecoded: number
  at: number
}

export interface FeedSnapshot {
  state: FeedState
  failure: FailureKind
  message: string | null
  rendition: Rendition
  attempt: number
  nextRetryS: number | null
  stats: FeedStats | null
  stalledMs: number
  signallingMs: number | null
}

export const BACKOFF_S = [1, 2, 4, 8, 8, 8]
const STALL_MS = 1500
const NOMINAL_FPS: Record<Rendition, number> = { hi: 15, lo: 7 }

export class WhepPlayer {
  private pc: RTCPeerConnection | null = null
  private session: string | null = null
  private statsTimer: number | null = null
  private retryTimer: number | null = null
  private stopped = false
  private lastDecoded = { frames: 0, at: 0 }
  private lastBytes = { bytes: 0, at: 0 }
  private lastPackets = { received: 0, lost: 0 }
  private bad = 0
  private good = 0
  /** Best frame rate this feed has delivered: "low fps" is judged against the
   * source itself (a 3 fps simulated camera is not a degraded 15 fps stream). */
  private peakFps = 0
  snap: FeedSnapshot = { state: 'idle', failure: null, message: null, rendition: 'hi', attempt: 0, nextRetryS: null, stats: null, stalledMs: 0, signallingMs: null }
  /** 'auto' follows link quality; a fixed rendition is an operator override. */
  mode: 'auto' | Rendition = 'auto'

  private readonly feedId: string
  private readonly video: HTMLVideoElement
  private readonly onChange: (s: FeedSnapshot) => void

  constructor(feedId: string, video: HTMLVideoElement, onChange: (s: FeedSnapshot) => void) {
    this.feedId = feedId
    this.video = video
    this.onChange = onChange
  }

  start(): void {
    this.stopped = false
    void this.connect()
  }

  stop(): void {
    this.stopped = true
    this.clearTimers()
    this.teardown()
    this.update({ state: 'disconnected', failure: null, message: null })
  }

  setMode(mode: 'auto' | Rendition): void {
    this.mode = mode
    const want: Rendition = mode === 'auto' ? this.snap.rendition : mode
    if (want !== this.snap.rendition) this.switchRendition(want)
  }

  private update(patch: Partial<FeedSnapshot>): void {
    this.snap = { ...this.snap, ...patch }
    this.onChange(this.snap)
  }

  private clearTimers(): void {
    if (this.statsTimer !== null) window.clearInterval(this.statsTimer)
    if (this.retryTimer !== null) window.clearTimeout(this.retryTimer)
    this.statsTimer = this.retryTimer = null
  }

  private teardown(): void {
    if (this.session) void mc.whepClose(this.session)
    this.session = null
    this.pc?.close()
    this.pc = null
    this.video.srcObject = null
  }

  private async connect(): Promise<void> {
    if (this.stopped) return
    this.teardown()
    this.update({ state: this.snap.attempt > 0 ? 'reconnecting' : 'connecting', nextRetryS: null })
    const pc = new RTCPeerConnection({ iceServers: [] })
    this.pc = pc
    pc.addTransceiver('video', { direction: 'recvonly' })
    pc.ontrack = (ev) => {
      if (this.pc !== pc) return
      this.video.srcObject = ev.streams[0] ?? new MediaStream([ev.track])
      void this.video.play().catch(() => undefined)
    }
    pc.onconnectionstatechange = () => {
      if (this.pc !== pc) return
      if (pc.connectionState === 'failed' || pc.connectionState === 'disconnected' || pc.connectionState === 'closed') {
        this.fail('media', `WebRTC connection ${pc.connectionState}`)
      }
    }
    try {
      await pc.setLocalDescription(await pc.createOffer())
      await waitIce(pc, 1500)
      const answer = await mc.whepOffer(this.feedId, pc.localDescription?.sdp ?? '', this.snap.rendition)
      if (this.pc !== pc || this.stopped) return
      this.session = answer.session
      await pc.setRemoteDescription({ type: 'answer', sdp: answer.sdp })
      this.lastDecoded = { frames: 0, at: performance.now() }
      this.lastPackets = { received: 0, lost: 0 }
      this.lastBytes = { bytes: 0, at: performance.now() }
      this.update({ signallingMs: answer.signalling_ms })
      this.statsTimer = window.setInterval(() => void this.poll(pc), 500)
    } catch (err) {
      if (this.pc !== pc) return
      const api = err instanceof ApiError ? err : null
      if (api?.code === 'SOURCE_UNAVAILABLE' || api?.status === 404) this.fail('source', 'Source not publishing (camera/source offline)')
      else this.fail('signalling', api ? `Signalling failed: ${api.message}` : `Signalling failed: ${err instanceof Error ? err.message : err}`)
    }
  }

  private fail(kind: FailureKind, message: string): void {
    if (this.stopped) return
    if (this.statsTimer !== null) window.clearInterval(this.statsTimer)
    this.statsTimer = null
    this.teardown()
    const attempt = this.snap.attempt + 1
    if (attempt > BACKOFF_S.length) {
      this.update({ state: 'failed', failure: kind, message, attempt, nextRetryS: null })
      return
    }
    const delay = BACKOFF_S[attempt - 1]
    this.update({ state: 'reconnecting', failure: kind, message, attempt, nextRetryS: delay })
    this.retryTimer = window.setTimeout(() => void this.connect(), delay * 1000)
  }

  /** Manual retry after FAILED (resets the back-off). */
  retry(): void {
    this.clearTimers()
    this.update({ attempt: 0, failure: null, message: null })
    void this.connect()
  }

  private switchRendition(r: Rendition): void {
    this.bad = this.good = 0
    this.update({ rendition: r })
    this.clearTimers()
    void this.connect()
  }

  private async poll(pc: RTCPeerConnection): Promise<void> {
    if (this.pc !== pc) return
    const report = await pc.getStats()
    const now = performance.now()
    let inbound: Record<string, number> | null = null
    let rtt: number | null = null
    report.forEach((s) => {
      if (s.type === 'inbound-rtp' && s.kind === 'video') inbound = s as unknown as Record<string, number>
      if (s.type === 'candidate-pair' && s.nominated && typeof s.currentRoundTripTime === 'number') rtt = s.currentRoundTripTime * 1000
    })
    if (!inbound) {
      this.checkStall(now, 0)
      return
    }
    const ib = inbound as Record<string, number>
    const frames = ib.framesDecoded ?? 0
    const bytes = ib.bytesReceived ?? 0
    const bitrate = now > this.lastBytes.at ? ((bytes - this.lastBytes.bytes) * 8) / (now - this.lastBytes.at) : null // kbit/s
    const recvDelta = (ib.packetsReceived ?? 0) - this.lastPackets.received
    const lostDelta = (ib.packetsLost ?? 0) - this.lastPackets.lost
    const lossPct = recvDelta + lostDelta > 0 ? (100 * Math.max(0, lostDelta)) / (recvDelta + lostDelta) : 0
    this.lastBytes = { bytes, at: now }
    this.lastPackets = { received: ib.packetsReceived ?? 0, lost: ib.packetsLost ?? 0 }
    const stats: FeedStats = {
      fps: ib.framesPerSecond ?? null, width: ib.frameWidth ?? null, height: ib.frameHeight ?? null,
      bitrateKbps: bitrate !== null ? Math.round(bitrate) : null, rttMs: rtt, jitterMs: ib.jitter != null ? ib.jitter * 1000 : null,
      lossPct, framesDropped: ib.framesDropped ?? null, framesDecoded: frames, at: Date.now(),
    }
    this.checkStall(now, frames)
    if (this.pc !== pc) return // the stall check failed this session over
    // Quality ladder (auto mode only).
    if (stats.fps !== null && this.snap.rendition === 'hi') this.peakFps = Math.max(this.peakFps, stats.fps)
    const nominal = this.snap.rendition === 'hi' ? this.peakFps : Math.min(this.peakFps / 2 || NOMINAL_FPS.lo, NOMINAL_FPS.lo)
    const lowFps = stats.fps !== null && frames > 30 && nominal > 0 && stats.fps < nominal / 2
    const isBad = lossPct > 5 || (rtt ?? 0) > 400 || lowFps
    if (isBad) {
      this.bad++
      this.good = 0
    } else {
      this.good++
      this.bad = 0
    }
    if (this.mode === 'auto' && this.snap.rendition === 'hi' && this.bad >= 3) {
      this.update({ stats })
      this.switchRendition('lo')
      return
    }
    if (this.mode === 'auto' && this.snap.rendition === 'lo' && this.good >= 10 && lossPct < 1 && (rtt ?? 0) < 200) {
      this.update({ stats })
      this.switchRendition('hi')
      return
    }
    const state: FeedState = frames > 0 ? (this.snap.rendition === 'lo' || isBad ? 'degraded' : 'connected') : this.snap.state
    this.update({ stats, state, failure: frames > 0 ? null : this.snap.failure, attempt: frames > 0 ? 0 : this.snap.attempt, message: frames > 0 ? null : this.snap.message })
  }

  private checkStall(now: number, frames: number): void {
    if (frames > this.lastDecoded.frames) {
      this.lastDecoded = { frames, at: now }
      if (this.snap.stalledMs) this.update({ stalledMs: 0 })
      return
    }
    const stalled = now - this.lastDecoded.at
    this.update({ stalledMs: stalled })
    if (stalled > STALL_MS && (this.snap.state === 'connected' || this.snap.state === 'degraded' || this.lastDecoded.frames === 0 && stalled > 6000)) {
      this.fail('media', this.lastDecoded.frames ? 'Video link lost: no frames decoded' : 'No video received')
    }
  }
}

function waitIce(pc: RTCPeerConnection, timeoutMs: number): Promise<void> {
  if (pc.iceGatheringState === 'complete') return Promise.resolve()
  return new Promise((resolve) => {
    const t = window.setTimeout(resolve, timeoutMs)
    pc.addEventListener('icegatheringstatechange', () => {
      if (pc.iceGatheringState === 'complete') {
        window.clearTimeout(t)
        resolve()
      }
    })
  })
}
