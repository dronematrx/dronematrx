import { create } from 'zustand'
import * as mc from '../api/missionControl'
import type { Detection, DroneCommand, McOverview, VideoFeeds } from '../api/missionControl'
import { realtimeEvents } from '../telemetry/realtimeEvents'

/** Mission-control state that the fleet store does not hold: per-drone
 * source/binding/mission metadata (REST), selection shared by the three
 * panels, command lifecycles and detections (pushed over the one shared
 * WebSocket). Drone positions still come from useDroneStore. */
export interface DetectionTiming {
  inferredMs: number
  clientReceivedMs: number
  renderedMs?: number
}

interface McState {
  overview: McOverview | null
  overviewAt: number
  overviewError: string | null
  selectedDroneId: string | null
  commands: Record<string, DroneCommand>
  detections: Detection[]
  detectionTimings: Record<string, DetectionTiming>
  /** Bumped when the shared WebSocket reconnects: events sent during the gap are lost, so views re-read. */
  resyncVersion: number
  feeds: VideoFeeds | null
  layers: { tracks: boolean; routes: boolean; geofences: boolean; detections: boolean; heatmap: boolean }
  heatmapVersion: number
  /** Reported positions per drone (telemetry only, never interpolated). */
  tracks: Record<string, [number, number][]>
  appendTracks: (points: { id: string; lat: number | null; lon: number | null; connected: boolean }[]) => void
  error: string | null
  select: (id: string | null) => void
  loadOverview: () => Promise<void>
  loadDetections: () => Promise<void>
  loadFeeds: () => Promise<void>
  trackCommand: (c: DroneCommand) => void
  toggleLayer: (k: keyof McState['layers']) => void
  markRendered: (id: string) => void
  setError: (e: string | null) => void
  subscribe: () => () => void
}

export const useMissionControlStore = create<McState>((set, get) => ({
  overview: null,
  overviewAt: 0,
  overviewError: null,
  selectedDroneId: null,
  commands: {},
  detections: [],
  detectionTimings: {},
  resyncVersion: 0,
  feeds: null,
  layers: { tracks: true, routes: true, geofences: true, detections: true, heatmap: false },
  heatmapVersion: 0,
  tracks: {},
  error: null,

  appendTracks: (points) => {
    let changed = false
    const next = { ...get().tracks }
    for (const p of points) {
      if (p.lat == null || p.lon == null || !p.connected) continue
      const t = next[p.id] ?? []
      const last = t[t.length - 1]
      if (last && last[0] === p.lat && last[1] === p.lon) continue
      next[p.id] = [...t.slice(-399), [p.lat, p.lon]]
      changed = true
    }
    if (changed) set({ tracks: next })
  },

  select: (id) => set({ selectedDroneId: id }),
  setError: (e) => set({ error: e }),
  toggleLayer: (k) => set({ layers: { ...get().layers, [k]: !get().layers[k] } }),

  loadOverview: async () => {
    try {
      const overview = await mc.getOverview()
      set({ overview, overviewAt: Date.now(), overviewError: null })
    } catch (err) {
      set({ overviewError: err instanceof Error ? err.message : 'Mission control unavailable' })
    }
  },

  loadDetections: async () => {
    try {
      set({ detections: await mc.listDetections() })
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Detections unavailable' })
    }
  },

  loadFeeds: async () => {
    try {
      set({ feeds: await mc.getFeeds() })
    } catch (err) {
      set({ feeds: { relay: 'unavailable', feeds: [], camera_configured: false }, error: err instanceof Error ? err.message : 'Video unavailable' })
    }
  },

  trackCommand: (c) => set({ commands: { ...get().commands, [c.id]: { ...get().commands[c.id], ...c } } }),

  markRendered: (id) => {
    const t = get().detectionTimings[id]
    if (t && t.renderedMs === undefined) set({ detectionTimings: { ...get().detectionTimings, [id]: { ...t, renderedMs: Date.now() } } })
  },

  subscribe: () => {
    const onCommand = (e: Event) => {
      const c = (e as CustomEvent<DroneCommand>).detail
      if (c?.id) get().trackCommand(c)
    }
    const onDetection = (e: Event) => {
      const d = (e as CustomEvent<Detection>).detail
      if (!d?.id) return
      const now = Date.now()
      const exists = get().detections.some((x) => x.id === d.id)
      set({
        detections: exists ? get().detections.map((x) => (x.id === d.id ? d : x)) : [d, ...get().detections].slice(0, 300),
        detectionTimings: exists ? get().detectionTimings : { ...get().detectionTimings, [d.id]: { inferredMs: Date.parse(d.inferred_at), clientReceivedMs: now } },
      })
    }
    const onReview = (e: Event) => {
      const d = (e as CustomEvent<Detection>).detail
      if (!d?.id) return
      set({ detections: get().detections.map((x) => (x.id === d.id ? { ...x, ...d } : x)), heatmapVersion: get().heatmapVersion + 1 })
    }
    const onConnection = (e: Event) => {
      if (!(e as CustomEvent<boolean>).detail) return
      set({ resyncVersion: get().resyncVersion + 1 })
      void get().loadDetections()
    }
    realtimeEvents.addEventListener('connection', onConnection)
    realtimeEvents.addEventListener('command', onCommand)
    realtimeEvents.addEventListener('detection', onDetection)
    realtimeEvents.addEventListener('detection_review', onReview)
    return () => {
      realtimeEvents.removeEventListener('connection', onConnection)
      realtimeEvents.removeEventListener('command', onCommand)
      realtimeEvents.removeEventListener('detection', onDetection)
      realtimeEvents.removeEventListener('detection_review', onReview)
    }
  },
}))
