import { useEffect, useMemo, useState } from 'react'
import { Check, X } from 'lucide-react'
import * as mc from '../../api/missionControl'
import type { Detection, ReviewStatus } from '../../api/missionControl'
import { useMissionControlStore } from '../../store/useMissionControlStore'

declare global {
  interface Window { __dmxDetectionLatency?: { id: string; inferenceToCardMs: number; inferenceToSocketMs: number; provider: string }[] }
}

type Filter = 'pending' | 'all' | ReviewStatus

export function DetectionFeed({ canReview, onLocate }: { canReview: boolean; onLocate: (lat: number, lon: number) => void }) {
  const detections = useMissionControlStore((s) => s.detections)
  const [filter, setFilter] = useState<Filter>('pending')
  const shown = useMemo(() => detections.filter((d) => filter === 'all' || d.review_status === filter), [detections, filter])
  const counts = useMemo(() => ({
    pending: detections.filter((d) => d.review_status === 'pending').length,
    accepted: detections.filter((d) => d.review_status === 'accepted').length,
    rejected: detections.filter((d) => d.review_status === 'rejected').length,
  }), [detections])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minHeight: 0 }} data-testid="mc-detection-feed">
      <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }} role="tablist" aria-label="Detection filter">
        {(['pending', 'accepted', 'rejected', 'all'] as Filter[]).map((f) => (
          <button key={f} type="button" role="tab" aria-selected={filter === f} onClick={() => setFilter(f)} data-testid={`det-filter-${f}`}
            className="dm-btn dm-btn--ghost" style={{ padding: '.15rem .45rem', fontSize: '.62rem', outline: filter === f ? '1px solid #FFD08A' : undefined }}>
            {f} {f !== 'all' ? `(${counts[f]})` : `(${detections.length})`}
          </button>
        ))}
      </div>
      {!shown.length && <p style={{ fontSize: '.72rem', opacity: 0.75 }} data-testid="det-empty">No {filter === 'all' ? '' : filter} detections.</p>}
      <div style={{ display: 'grid', gap: 5 }}>
        {shown.map((d) => <DetectionCard key={d.id} d={d} canReview={canReview} onLocate={onLocate} />)}
      </div>
    </div>
  )
}

function DetectionCard({ d, canReview, onLocate }: { d: Detection; canReview: boolean; onLocate: (lat: number, lon: number) => void }) {
  const [thumb, setThumb] = useState<string | null>(null)
  const [thumbErr, setThumbErr] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const markRendered = useMissionControlStore((s) => s.markRendered)
  const timing = useMissionControlStore((s) => s.detectionTimings[d.id])

  useEffect(() => {
    if (!d.has_thumbnail) return
    let url: string | null = null
    mc.thumbnailUrl(d.id).then((u) => { url = u; setThumb(u) }).catch(() => setThumbErr(true))
    return () => { if (url) URL.revokeObjectURL(url) }
  }, [d.id, d.has_thumbnail])

  // Arrival latency: inference timestamp -> this card visible (after paint).
  useEffect(() => {
    if (!timing || timing.renderedMs !== undefined) return
    requestAnimationFrame(() => {
      const shownAt = Date.now()
      markRendered(d.id)
      ;(window.__dmxDetectionLatency ??= []).push({ id: d.id, inferenceToCardMs: shownAt - timing.inferredMs,
        inferenceToSocketMs: timing.clientReceivedMs - timing.inferredMs, provider: d.provider })
    })
  }, [d.id, d.provider, timing, markRendered])

  const review = async (decision: 'accepted' | 'rejected') => {
    setBusy(true)
    setError(null)
    try {
      const r = await mc.reviewDetection(d.id, decision, decision === 'rejected' ? reason || undefined : undefined)
      useMissionControlStore.setState((s) => ({ detections: s.detections.map((x) => (x.id === d.id ? { ...x, ...r } : x)), heatmapVersion: s.heatmapVersion + 1 }))
      setRejecting(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const hasLoc = d.latitude != null && d.longitude != null
  return (
    <div className="dm-det-card" data-status={d.review_status} data-testid={`det-card-${d.event_id}`} data-detection-id={d.id}>
      {thumb ? <img src={thumb} alt={`${d.label} thumbnail`} data-testid="det-thumb" /> : <div className="dm-det-thumb-missing" data-testid="det-thumb-missing">{d.has_thumbnail && !thumbErr ? 'loading…' : 'no thumbnail'}</div>}
      <div style={{ display: 'grid', gap: 2, minWidth: 0 }}>
        <div style={{ display: 'flex', gap: 5, alignItems: 'center', flexWrap: 'wrap' }}>
          <strong data-testid="det-label">{d.label}</strong>
          <span className="dm-mono" data-testid="det-confidence">{(d.confidence * 100).toFixed(1)}%</span>
          {d.test_fixture ? <span className="dm-src dm-src--none" style={{ color: '#FFD08A', borderColor: '#FFD08A' }}>TEST FIXTURE</span> : <span className="dm-src dm-src--sitl">SITL CV DETECTOR</span>}
          <span className={`dm-cmd-state dm-cmd-state--${d.review_status === 'accepted' ? 'confirmed' : d.review_status === 'rejected' ? 'rejected' : 'requested'}`} data-testid="det-status">{d.review_status.toUpperCase()}</span>
        </div>
        <span className="dm-mono" style={{ fontSize: '.6rem', opacity: 0.75 }} title={d.event_id}>#{d.id.slice(0, 8)} · {d.drone_name ?? 'unknown drone'}{d.mission_id ? ` · mission ${d.mission_id.slice(0, 8)}` : ' · no active mission'}</span>
        <span className="dm-mono" style={{ fontSize: '.6rem' }} data-testid="det-inferred">inferred {new Date(d.inferred_at).toLocaleTimeString()}{d.reviewed_at ? ` · reviewed ${new Date(d.reviewed_at).toLocaleTimeString()}` : ''}</span>
        {hasLoc ? (
          <button type="button" className="dm-mono" onClick={() => onLocate(d.latitude!, d.longitude!)} data-testid="det-location"
            style={{ fontSize: '.6rem', background: 'none', border: 0, color: '#7FB3FF', padding: 0, textAlign: 'left', cursor: 'pointer' }}>{d.latitude!.toFixed(6)}, {d.longitude!.toFixed(6)}</button>
        ) : (
          <span style={{ fontSize: '.6rem', color: '#FFD08A' }} data-testid="det-no-location">No valid location: {d.location_error ?? 'not supplied'}</span>
        )}
        {d.reject_reason && <span style={{ fontSize: '.6rem', opacity: 0.8 }}>Reason: {d.reject_reason}</span>}
        {error && <span className="dm-error" style={{ fontSize: '.6rem' }}>{error}</span>}
        {canReview && d.review_status === 'pending' && (
          rejecting ? (
            <div style={{ display: 'flex', gap: 3 }}>
              <input className="dm-input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (optional)" style={{ flex: 1, fontSize: '.62rem', padding: '.1rem .3rem' }} data-testid="det-reject-reason" />
              <button type="button" className="dm-btn dm-btn--danger" style={{ padding: '.1rem .4rem' }} disabled={busy} onClick={() => void review('rejected')} data-testid="det-reject-confirm">Reject</button>
              <button type="button" className="dm-btn dm-btn--ghost" style={{ padding: '.1rem .4rem' }} onClick={() => setRejecting(false)}>Cancel</button>
            </div>
          ) : (
            <div style={{ display: 'flex', gap: 3 }}>
              <button type="button" className="dm-btn dm-btn--primary" style={{ padding: '.1rem .45rem' }} disabled={busy} onClick={() => void review('accepted')} data-testid="det-accept"><Check size={11} /> Accept</button>
              <button type="button" className="dm-btn" style={{ padding: '.1rem .45rem' }} disabled={busy} onClick={() => setRejecting(true)} data-testid="det-reject"><X size={11} /> Reject</button>
            </div>
          )
        )}
      </div>
    </div>
  )
}
