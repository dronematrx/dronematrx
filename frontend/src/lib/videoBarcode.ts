/** Reads the timestamp barcode burnt into TEST PATTERN frames
 * (sim/simhost/video.py: encode_barcode). 64 cells in one row at the top
 * left of a 480x270 frame; cell i white = bit i. Bits 0..47: capture time
 * (epoch ms, simhost clock). Bits 48..63: frame counter. */
export const BARCODE_BITS = 64
const CELL_W = 6
const CELL_H = 16
const BAR_X = 8
const BAR_Y = 8
const REF_WIDTH = 480

export interface BarcodeValue {
  captureMs: number
  counter: number
}

export function decodeBarcode(pixels: Uint8ClampedArray, width: number): BarcodeValue | null {
  const scale = width / REF_WIDTH
  let ms = 0
  let counter = 0
  let whites = 0
  for (let i = 0; i < BARCODE_BITS; i++) {
    const x = Math.floor((BAR_X + i * CELL_W + CELL_W / 2) * scale)
    const y = Math.floor((BAR_Y + CELL_H / 2) * scale)
    const o = (y * width + x) * 4
    const lum = (pixels[o] + pixels[o + 1] + pixels[o + 2]) / 3
    if (lum > 128) {
      whites++
      if (i < 48) ms += 2 ** i
      else counter += 2 ** (i - 48)
    }
  }
  // Plausibility: a 2020-2040 epoch ms value, and not an all-white region.
  if (whites === BARCODE_BITS || ms < 1.5e12 || ms > 2.3e12) return null
  return { captureMs: ms, counter }
}

let canvas: HTMLCanvasElement | null = null

/** Decode the frame currently shown by `video` (same-origin MediaStream). */
export function readVideoBarcode(video: HTMLVideoElement): BarcodeValue | null {
  const w = video.videoWidth
  const h = video.videoHeight
  if (!w || !h) return null
  canvas ??= document.createElement('canvas')
  const rows = Math.min(h, Math.ceil(((BAR_Y + CELL_H) * w) / REF_WIDTH) + 2)
  canvas.width = w
  canvas.height = rows
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx) return null
  ctx.drawImage(video, 0, 0, w, rows, 0, 0, w, rows)
  return decodeBarcode(ctx.getImageData(0, 0, w, rows).data, w)
}
