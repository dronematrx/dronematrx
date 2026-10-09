import { Radio } from 'lucide-react'
import type { CommsSnapshot, DroneLinkState, LinkType } from '../../api/types'

import { LINK_LABEL } from '../../lib/opsFormat'

export function LinkPill({ link }: { link: LinkType }) {
  return <span className={`dm-link-pill dm-link-pill--${link}`}>{LINK_LABEL[link]}</span>
}

function LinkCard({ state, canWrite, busy, onDegrade, onRestore }: {
  state: DroneLinkState
  canWrite: boolean
  busy: boolean
  onDegrade: (link: LinkType) => void
  onRestore: () => void
}) {
  return (
    <div className="dm-card" style={{ background: 'var(--dm-navy-900)', padding: 'var(--dm-space-sm) var(--dm-space-md)' }} data-testid={`link-card-${state.name}`}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '.35rem' }}>
        <strong style={{ letterSpacing: '.05em' }}>{state.name}</strong>
        <span data-testid={`active-link-${state.name}`}>
          <LinkPill link={state.active_link} />
        </span>
      </div>
      <div className="dm-kv">
        <span>RSSI</span>
        <span className="dm-mono">{state.rssi_dbm?.toFixed(0) ?? '--'} dBm</span>
      </div>
      <div className="dm-kv">
        <span>Latency</span>
        <span className="dm-mono">{state.latency_ms?.toFixed(0) ?? '--'} ms</span>
      </div>
      <div className="dm-kv">
        <span>Packet loss</span>
        <span className="dm-mono">{state.packet_loss_pct?.toFixed(1) ?? '--'} %</span>
      </div>
      <div className="dm-kv">
        <span>Link state</span>
        <span className="dm-mono" style={{ textTransform: 'uppercase', color: state.all_links_lost ? '#FFB3AA' : undefined }}>
          {state.all_links_lost ? 'ALL LINKS LOST' : state.link_state}
        </span>
      </div>
      <div className="dm-mono" style={{ fontSize: '.65rem', opacity: 0.75, marginTop: '.3rem' }}>
        {(['wifi', 'lte', 'lora'] as LinkType[]).map((l) => {
          const s = state.links[l]
          return s ? `${LINK_LABEL[l]} ${s.rssi_dbm.toFixed(0)}dBm ${s.quality ?? ''}`.trim() : null
        }).filter(Boolean).join(' · ')}
      </div>
      {canWrite && (
        <div style={{ display: 'flex', gap: '.25rem', marginTop: '.4rem', flexWrap: 'wrap' }}>
          <button type="button" className="dm-btn" style={{ padding: '.25rem .45rem', fontSize: '.65rem' }} disabled={busy} onClick={() => onDegrade('wifi')} data-testid={`degrade-wifi-${state.name}`}>
            Degrade Wi-Fi
          </button>
          <button type="button" className="dm-btn" style={{ padding: '.25rem .45rem', fontSize: '.65rem' }} disabled={busy} onClick={() => onDegrade('lte')} data-testid={`degrade-lte-${state.name}`}>
            Degrade LTE
          </button>
          <button type="button" className="dm-btn dm-btn--ghost" style={{ padding: '.25rem .45rem', fontSize: '.65rem' }} disabled={busy} onClick={onRestore} data-testid={`restore-${state.name}`}>
            Restore
          </button>
        </div>
      )}
    </div>
  )
}

/** Per-drone communication status, live from the communication manager (2 Hz). */
export function FleetWall({ comms, canWrite, busy, onDegrade, onRestore }: {
  comms: CommsSnapshot | null
  canWrite: boolean
  busy: boolean
  onDegrade: (droneId: string, link: LinkType) => void
  onRestore: (droneId: string) => void
}) {
  if (!comms) {
    return <p style={{ opacity: 0.7, fontSize: '.8rem' }}>Communication manager offline.</p>
  }
  return (
    <section aria-label="Fleet communication wall">
      <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', margin: '0 0 .5rem', textTransform: 'uppercase', fontSize: '.85rem', letterSpacing: '.06em' }}>
        <Radio size={16} /> Fleet Wall · Links&hellip;
        <span className="dm-mono" style={{ marginLeft: 'auto', textTransform: 'none', fontSize: '.7rem', opacity: 0.75 }}>
          monitor {comms.timing.measured_hz?.toFixed(2) ?? '--'} Hz
        </span>
      </div>
      <div className="dm-fleet-wall" data-testid="fleet-wall">
        {comms.drones.map((d) => (
          <LinkCard key={d.drone_id} state={d} canWrite={canWrite} busy={busy} onDegrade={(l) => onDegrade(d.drone_id, l)} onRestore={() => onRestore(d.drone_id)} />
        ))}
      </div>
    </section>
  )
}
