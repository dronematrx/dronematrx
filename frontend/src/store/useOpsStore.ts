import { create } from 'zustand'
import * as opsApi from '../api/ops'
import type { CommsSnapshot, CommsSwitch, LinkType, OrcaEvent, OrcaView } from '../api/types'
import { realtimeEvents } from '../telemetry/realtimeEvents'

/** Live fleet operations (ORCA + comms) fed by the shared WebSocket. */
interface OpsState {
  orca: OrcaView | null
  comms: CommsSnapshot | null
  timeline: OrcaEvent[]
  switches: CommsSwitch[]
  busy: boolean
  error: string | null
  refresh: () => Promise<void>
  startDemo: () => Promise<void>
  stopDemo: () => Promise<void>
  degrade: (droneId: string, link: LinkType) => Promise<void>
  restore: (droneId: string) => Promise<void>
  command: (droneId: string, cmd: 'HOLD' | 'RTH' | 'RESUME') => Promise<void>
}

const DEGRADE_RSSI: Record<LinkType, number> = { wifi: -84, lte: -114, lora: -130 }

function mergeTimeline(current: OrcaEvent[], incoming: OrcaEvent[]): OrcaEvent[] {
  const bySeq = new Map(current.map((e) => [e.seq, e]))
  for (const e of incoming) bySeq.set(e.seq, e)
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq).slice(-200)
}

export const useOpsStore = create<OpsState>((set, get) => {
  async function run(fn: () => Promise<unknown>) {
    set({ busy: true, error: null })
    try {
      await fn()
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Request failed' })
    } finally {
      set({ busy: false })
    }
  }

  return {
    orca: null,
    comms: null,
    timeline: [],
    switches: [],
    busy: false,
    error: null,

    refresh: async () => {
      try {
        const [orca, comms, events] = await Promise.all([opsApi.getOrcaState(), opsApi.getLinks(), opsApi.getCommsEvents()])
        set({ orca, comms, timeline: mergeTimeline(get().timeline, orca.events), switches: events.switches, error: null })
      } catch (err) {
        set({ error: err instanceof Error ? err.message : 'Live runtime unavailable' })
      }
    },
    startDemo: () =>
      run(async () => {
        set({ timeline: [] })
        await opsApi.startDemo()
        await get().refresh()
      }),
    stopDemo: () => run(async () => void (await opsApi.stopDemo())),
    degrade: (droneId, link) => run(() => opsApi.degradeLink(droneId, link, DEGRADE_RSSI[link])),
    restore: (droneId) => run(() => opsApi.restoreLinks(droneId)),
    command: (droneId, cmd) => run(() => opsApi.sendCommand(droneId, cmd)),
  }
})

realtimeEvents.addEventListener('orca', (evt) => {
  const orca = (evt as CustomEvent<OrcaView>).detail
  useOpsStore.setState({ orca, timeline: mergeTimeline(useOpsStore.getState().timeline, orca.events ?? []) })
})
realtimeEvents.addEventListener('comms', (evt) => {
  useOpsStore.setState({ comms: (evt as CustomEvent<CommsSnapshot>).detail })
})
realtimeEvents.addEventListener('comms_event', (evt) => {
  const e = (evt as CustomEvent<CommsSwitch & { kind: string }>).detail
  if (e.kind === 'fallback' || e.kind === 'recovery') {
    useOpsStore.setState({ switches: [...useOpsStore.getState().switches, e].slice(-50) })
  }
})
