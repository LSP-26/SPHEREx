// Light curves and rough spectra for the variable-star targets, built offline by
// backend/offline/build_lightcurves.py and served as static JSON under /lightcurves/. Every point
// is one frame at its own wavelength and date, so the "light curve" mixes wavelengths (shown by
// colour) and the spectrum mixes epochs. Saturated stars give lower limits, not measurements.

export const LIGHTCURVE_BASE = `${import.meta.env.BASE_URL ?? "/"}lightcurves/`;

export interface LcPoint {
  frame_id: string;
  detector: number;
  t_mjd: number;
  lambda_um: number;
  flux_jy: number | null;
  err_jy: number | null;
  lower_limit: boolean;
  reason: string | null;
  flags: string[];
  /** "aperture" (2 px) or "large_aperture" (8 px, used when the ring is flagged). Older files lack it. */
  method?: string;
}

export interface SpectrumPoint {
  lambda_um: number;
  flux_jy: number | null;
  scatter_jy: number | null;
  n: number;
  lower_limit: boolean;
}

export interface Lightcurve {
  key: string;
  name: string;
  ra: number;
  dec: number;
  note: string;
  summary: { n_points: number; n_ok: number; n_lower_limit: number; n_refused: number };
  points: LcPoint[];
  spectrum: SpectrumPoint[];
  /** Only on live measurements (GET /measure): frames over the spot, and the thinning used. */
  n_frames_covering?: number;
  thinned_every?: number;
}

/** Where each built light curve is on the sky (J2000 position moved to 2026, as in the files). */
export const LIGHTCURVE_TARGETS: { key: string; name: string; ra: number; dec: number }[] = [
  { key: "r_dra", name: "R Draconis", ra: 248.1672, dec: 66.755 },
  { key: "rw_aur", name: "RW Aurigae", ra: 76.9566, dec: 30.4013 },
];

/** Angular separation in arcminutes (small-angle, fine at these scales). */
export function separationArcmin(ra1: number, dec1: number, ra2: number, dec2: number): number {
  const dra = (ra1 - ra2) * Math.cos(((dec1 + dec2) / 2) * (Math.PI / 180));
  return Math.hypot(dra, dec1 - dec2) * 60;
}

/** The light curve that belongs to this position, if the field is centred within 3 arcmin of one. */
export function matchLightcurve(ra: number, dec: number): { key: string; name: string } | null {
  let best: { key: string; name: string; d: number } | null = null;
  for (const t of LIGHTCURVE_TARGETS) {
    const d = separationArcmin(ra, dec, t.ra, t.dec);
    if (d <= 3 && (!best || d < best.d)) best = { key: t.key, name: t.name, d };
  }
  return best ? { key: best.key, name: best.name } : null;
}

export async function fetchLightcurve(key: string, fetchFn: typeof fetch = (...a) => fetch(...a)): Promise<
  { ok: true; data: Lightcurve } | { ok: false; error: string }
> {
  try {
    const res = await fetchFn(`${LIGHTCURVE_BASE}${key}.json`);
    if (!res.ok) return { ok: false, error: "The light curve file is not available." };
    const data = (await res.json()) as Lightcurve;
    if (!data || !Array.isArray(data.points)) return { ok: false, error: "The light curve file is not available." };
    return { ok: true, data };
  } catch {
    return { ok: false, error: "The light curve file could not be loaded." };
  }
}

export type MeasureResult = { ok: true; data: Lightcurve } | { ok: false; error: string };

/** Ask the live server to measure whatever is at ra, dec (GET /measure) and poll until it is done.
 * The server starts the run on the first call and answers every call with its state; polling
 * (not streaming) is what works through the Cloudflare tunnel. */
export async function measureHere(
  p: { ra: number; dec: number },
  o: {
    apiBase?: string; fetchFn?: typeof fetch; signal?: AbortSignal;
    onProgress?: (done: number, total: number) => void;
    sleep?: (ms: number) => Promise<void>; intervalMs?: number;
  } = {},
): Promise<MeasureResult> {
  const { apiBase = "", signal, onProgress, intervalMs = 2000 } = o;
  const fetchFn = o.fetchFn ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
  const sleep = o.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const url = `${apiBase}/measure?${new URLSearchParams({ ra: String(p.ra), dec: String(p.dec) })}`;
  for (;;) {
    let body: { status?: string; done?: number; total?: number; result?: Lightcurve; message?: string };
    try {
      const res = await fetchFn(url, { signal });
      if (!res.ok) {
        let why = "";
        try { why = ((await res.json()) as { message?: string }).message ?? ""; } catch { /* no body */ }
        if (res.status === 404 && !why) return { ok: false, error: "This server cannot measure yet. Restart it after updating." };
        return { ok: false, error: why || `The measurement failed (HTTP ${res.status}).` };
      }
      body = await res.json();
    } catch (e) {
      if ((e as Error)?.name === "AbortError") return { ok: false, error: "Stopped." };
      return { ok: false, error: "Measuring needs the live server, which could not be reached." };
    }
    if (body.status === "done" && body.result && Array.isArray(body.result.points)) return { ok: true, data: body.result };
    if (body.status === "error") return { ok: false, error: body.message || "The measurement failed." };
    if (body.status !== "running") return { ok: false, error: "The server's answer could not be read." };
    onProgress?.(body.done ?? 0, body.total ?? 0);
    await sleep(intervalMs);
    if (signal?.aborted) return { ok: false, error: "Stopped." };
  }
}

/** Points that can be drawn on a log flux axis. */
export function plottable<T extends { flux_jy: number | null }>(pts: T[]): (T & { flux_jy: number })[] {
  return pts.filter((p): p is T & { flux_jy: number } => p.flux_jy != null && p.flux_jy > 0);
}

/** Colour for a wavelength: blue at 0.75 um through to red at 5 um. */
export function wavelengthColour(lambdaUm: number): string {
  const t = Math.min(1, Math.max(0, (lambdaUm - 0.75) / (5 - 0.75)));
  return `hsl(${Math.round(240 - 240 * t)} 85% 62%)`;
}

/** One colour per detector (D1 to D6). Each detector sees its own band, so frames from different
 * detectors are different wavelengths and are not directly comparable: they are separate series. */
const DETECTOR_COLOURS = ["#60a5fa", "#38bdf8", "#34d399", "#fbbf24", "#fb923c", "#f87171"];
export function detectorColour(detector: number): string {
  return DETECTOR_COLOURS[(Math.max(1, Math.round(detector)) - 1) % DETECTOR_COLOURS.length] ?? "#94a3b8";
}

/** A point measured with the 8 px fallback aperture: its own series, never mixed with the 2 px points. */
export function isLargeAperture(p: { method?: string }): boolean {
  return p.method === "large_aperture";
}

export interface Scale {
  to(v: number): number;
  ticks: number[];
  lo: number;
  hi: number;
}

/** A linear scale onto [0, size] (pixels), with round tick values. */
export function linearScale(min: number, max: number, size: number, flip = false): Scale {
  const span = max - min || 1;
  const rawStep = span / 5;
  const mag = 10 ** Math.floor(Math.log10(rawStep));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= rawStep) ?? 10 * mag;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Number(v.toPrecision(12)));
  const to = (v: number) => (flip ? 1 - (v - lo) / (hi - lo) : (v - lo) / (hi - lo)) * size;
  return { to, ticks, lo, hi };
}

/** A log10 scale onto [0, size] pixels, flux up the page, ticks at 1, 2, 5 x 10^n. */
export function logScale(min: number, max: number, size: number): Scale {
  const lo = Math.log10(min) - 0.1;
  const hi = Math.log10(max) + 0.1;
  const ticks: number[] = [];
  for (let e = Math.floor(lo); e <= Math.ceil(hi); e++) {
    for (const m of [1, 2, 5]) {
      const v = m * 10 ** e;
      if (Math.log10(v) >= lo && Math.log10(v) <= hi) ticks.push(Number(v.toPrecision(6)));
    }
  }
  return { to: (v) => (1 - (Math.log10(v) - lo) / (hi - lo)) * size, ticks, lo: 10 ** lo, hi: 10 ** hi };
}
