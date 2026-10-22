import { create } from 'zustand'
import * as simApi from '../api/simulations'
import type { SimEvent, SimLiveDrone, SimSession, SimSnapshot, TrackPoint } from '../api/simulations'
import { ApiError } from '../api/client'
import { realtimeEvents } from '../telemetry/realtimeEvents'

/** Simulation session state. Live updates arrive on the single shared
 * /ws/telemetry socket ({type: "simulation"} snapshots at 1 Hz and
 * {type: "simulation_event"}); this store never opens its own socket and
 * never animates anything itself -- positions come from simulator telemetry. */

interface SimState {
  session: SimSession | null
  live: SimSnapshot | null
  tracks: Record<string, TrackPoint[]> // by uin
  events: SimEvent[]
  busy: string | null
  error: { message: string; code: string | null } | null
  load: (sessionId: string) => Promise<void>
  setSession: (session: SimSession | null) => void
  run: <T>(label: string, fn: () => Promise<T>) => Promise<T | null>
  clearError: () => void
  reset: () => void
}

let subscribed = false

function mergeTracks(tracks: Record<string, TrackPoint[]>, drones: SimLiveDrone[]): Record<string, TrackPoint[]> {
  let changed = false
  const next = { ...tracks }
  for (const d of drones) {
    if (!d.track_tail?.length) continue
    const prev = next[d.uin] ?? []
    const lastT = prev.length ? prev[prev.length - 1][0] : -Infinity
    const fresh = d.track_tail.filter((p) => p[0] > lastT)
    if (fresh.length) {
      next[d.uin] = [...prev, ...fresh]
      changed = true
    }
  }
  return changed ? next : tracks
}

export const useSimulationStore = create<SimState>((set, get) => {
  if (!subscribed) {
    subscribed = true
    realtimeEvents.addEventListener('simulation', (e) => {
      const snap = (e as CustomEvent<SimSnapshot>).detail
      const { session } = get()
      if (!session || snap.session_id !== session.id) return
      set({ live: snap, tracks: mergeTracks(get().tracks, snap.drones) })
      if (['completed', 'aborted', 'stopped', 'failed'].includes(snap.state)) {
        // Final state: reload the persisted session (report flag, cleanup).
        window.setTimeout(() => get().load(session.id), 1500)
      }
    })
    realtimeEvents.addEventListener('simulation_event', (e) => {
      const ev = (e as CustomEvent<SimEvent>).detail
      const { session, events } = get()
      if (!session || ev.session_id !== session.id || events.some((x) => x.id === ev.id)) return
      set({ events: [...events, ev] })
      if (['sitl_ready', 'running', 'mission_completed', 'aborted', 'stopped', 'failed', 'report_generated', 'world_generated'].includes(ev.event_type)) {
        get().load(session.id)
      }
    })
  }

  return {
    session: null,
    live: null,
    tracks: {},
    events: [],
    busy: null,
    error: null,

    load: async (sessionId) => {
      try {
        const [session, events] = await Promise.all([simApi.getSimSession(sessionId), simApi.getSimEvents(sessionId)])
        const tracks: Record<string, TrackPoint[]> = {}
        for (const inst of session.instances) tracks[inst.uin] = inst.track ?? []
        set({ session, events, tracks, live: session.live ?? get().live })
      } catch (err) {
        set({ error: { message: err instanceof Error ? err.message : 'Failed to load simulation', code: err instanceof ApiError ? err.code : null } })
      }
    },

    setSession: (session) => set({ session, live: session?.live ?? null, tracks: {}, events: [] }),

    run: async (label, fn) => {
      set({ busy: label, error: null })
      try {
        return await fn()
      } catch (err) {
        set({ error: { message: err instanceof Error ? err.message : String(err), code: err instanceof ApiError ? err.code : null } })
        return null
      } finally {
        set({ busy: null })
      }
    },

    clearError: () => set({ error: null }),
    reset: () => set({ session: null, live: null, tracks: {}, events: [], busy: null, error: null }),
  }
})
