import type { LinkType, OrcaView } from '../api/types'

export const LINK_LABEL: Record<LinkType, string> = { wifi: 'Wi-Fi', lte: 'LTE', lora: 'LoRa' }

/** Map an engine drone id to its fleet name for display. */
export function nameOf(view: OrcaView | null, id: string): string {
  return view?.drones.find((d) => d.drone_id === id)?.name ?? id.slice(0, 8)
}
