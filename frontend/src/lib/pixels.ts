// Pixel maths on the server's 8-bit frames. Pure: arrays in, RGBA out, so it is easy to test.
//
// Each frame PNG is grey + alpha. Alpha 0 means "no data here" (off the detector or a dead
// pixel), which must never be confused with dark sky. Every frame is stretched on its own by the
// server, so before two frames are compared each is put on a common footing: its sky level
// (median) is subtracted and the result divided by its sky noise (robust sigma).

export interface Gray {
  width: number;
  height: number;
  /** Grey value 0..255 per pixel. */
  v: Float32Array;
  /** True where the frame has data. */
  ok: Uint8Array;
}

export function grayFromRgba(data: Uint8ClampedArray | Uint8Array, width: number, height: number): Gray {
  const n = width * height;
  const v = new Float32Array(n);
  const ok = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    v[i] = data[i * 4];
    ok[i] = data[i * 4 + 3] > 0 ? 1 : 0;
  }
  return { width, height, v, ok };
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const s = values.slice().sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** Sky level and noise of a frame from its pixels with data (median and 1.4826 × MAD). */
export function skyStats(g: Gray): { level: number; sigma: number } {
  const vals: number[] = [];
  for (let i = 0; i < g.v.length; i++) if (g.ok[i]) vals.push(g.v[i]);
  const level = median(vals);
  const sigma = 1.4826 * median(vals.map((x) => Math.abs(x - level)));
  return { level, sigma: Math.max(sigma, 1) };
}

/** Grey for "no change" in a difference picture, and how many grey steps one sigma is worth. */
export const DIFF_MID = 128;
export const DIFF_GAIN = 22;

/** `a - b` after each is normalised by its own sky. Brighter now: white; brighter before: black.
 *  Where either frame has no data the pixel is transparent. */
export function difference(a: Gray, b: Gray): Uint8ClampedArray {
  if (a.width !== b.width || a.height !== b.height) throw new Error("frames differ in size");
  const sa = skyStats(a);
  const sb = skyStats(b);
  const out = new Uint8ClampedArray(a.v.length * 4);
  for (let i = 0; i < a.v.length; i++) {
    const both = a.ok[i] && b.ok[i];
    const d = ((a.v[i] - sa.level) / sa.sigma - (b.v[i] - sb.level) / sb.sigma) * DIFF_GAIN;
    const g = DIFF_MID + d;
    out[i * 4] = out[i * 4 + 1] = out[i * 4 + 2] = g;
    out[i * 4 + 3] = both ? 255 : 0;
  }
  return out;
}

/** Mean of the frames given, pixel by pixel, over the frames that have data there. */
export function meanOf(frames: Gray[]): Gray | null {
  if (frames.length === 0) return null;
  const { width, height } = frames[0];
  const n = width * height;
  const v = new Float32Array(n);
  const ok = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    let sum = 0;
    let k = 0;
    for (const f of frames) if (f.ok[i]) { sum += f.v[i]; k++; }
    if (k > 0) { v[i] = sum / k; ok[i] = 1; }
  }
  return { width, height, v, ok };
}

/** A false-colour picture: red = longest wavelengths, blue = shortest. A missing channel is black.
 *  A pixel is shown where at least one channel has data. */
export function composite(ch: { r: Gray | null; g: Gray | null; b: Gray | null }): Uint8ClampedArray {
  const ref = ch.r ?? ch.g ?? ch.b;
  if (!ref) throw new Error("no channel");
  const n = ref.width * ref.height;
  const out = new Uint8ClampedArray(n * 4);
  for (let i = 0; i < n; i++) {
    out[i * 4] = ch.r?.ok[i] ? ch.r.v[i] : 0;
    out[i * 4 + 1] = ch.g?.ok[i] ? ch.g.v[i] : 0;
    out[i * 4 + 2] = ch.b?.ok[i] ? ch.b.v[i] : 0;
    out[i * 4 + 3] = ch.r?.ok[i] || ch.g?.ok[i] || ch.b?.ok[i] ? 255 : 0;
  }
  return out;
}

/** The frame as it came from the server, as RGBA (grey, transparent where there is no data). */
export function asRgba(g: Gray): Uint8ClampedArray {
  const out = new Uint8ClampedArray(g.v.length * 4);
  for (let i = 0; i < g.v.length; i++) {
    out[i * 4] = out[i * 4 + 1] = out[i * 4 + 2] = g.v[i];
    out[i * 4 + 3] = g.ok[i] ? 255 : 0;
  }
  return out;
}
