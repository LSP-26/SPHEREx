// Known asteroids and comets in the field, from IMCCE's SkyBoT cone search, asked straight from the
// browser (needs the internet, not our server, and the service must allow it from this page). A
// main-belt asteroid crosses a 5 arcmin field in about half a day, so it is a different dot in
// every frame: one request per frame date, each answering "what is in this field at that moment".

import { pictureSizePx, skyToPicture } from "./sky";

const ENDPOINT = "https://ssp.imcce.fr/webservices/skybot/api/conesearch.php";

export interface Asteroid {
  name: string;
  ra: number;   // degrees
  dec: number;  // degrees
  vmag: number | null;
}

/** Frames within about an hour of each other share one request. */
export const epochKey = (mjd: number): number => Math.round(mjd / 0.05) * 0.05;

/** "08 55 10.8" or "08:55:10.8" or a plain number, to a number (in the unit of the field). */
export function parseSexagesimal(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v !== "string") return null;
  const t = v.trim();
  if (t === "") return null;
  const sign = t.startsWith("-") ? -1 : 1;
  const parts = t.replace(/^[+-]/, "").split(/[\s:hdms'"°]+/).filter(Boolean).map(Number);
  if (parts.length === 0 || parts.some((p) => !Number.isFinite(p))) return null;
  const [a, b = 0, c = 0] = parts;
  return sign * (a + b / 60 + c / 3600);
}

function field(row: Record<string, unknown>, prefix: string): { key: string; value: unknown } | null {
  const key = Object.keys(row).find((k) => k.toLowerCase().startsWith(prefix));
  return key ? { key, value: row[key] } : null;
}

/** Rows of a SkyBoT JSON answer to asteroids. Tolerant of the exact wrapper and of hours versus degrees. */
export function parseSkybot(body: unknown): Asteroid[] {
  const rows: unknown[] = Array.isArray(body) ? body
    : body && typeof body === "object"
      ? ((body as Record<string, unknown>).data ?? (body as Record<string, unknown>).results
        ?? (body as Record<string, unknown>).rows ?? []) as unknown[]
      : [];
  const out: Asteroid[] = [];
  for (const r of rows) {
    if (!r || typeof r !== "object") continue;
    const row = r as Record<string, unknown>;
    const ra = field(row, "ra"), dec = field(row, "dec"), name = field(row, "name"), v = field(row, "vmag");
    if (!ra || !dec) continue;
    let raV = parseSexagesimal(ra.value);
    const decV = parseSexagesimal(dec.value);
    if (raV == null || decV == null) continue;
    // "RA (hour)" is in hours; anything else is taken as degrees
    if (/hour|\bh\b/i.test(ra.key) || (typeof ra.value === "string" && /[\s:]/.test(ra.value.trim()))) raV *= 15;
    const vm = v ? parseSexagesimal(v.value) : null;
    out.push({ name: name ? String(name.value).trim() : "unnamed", ra: raV, dec: decV, vmag: vm });
  }
  return out;
}

export type AsteroidResult = { ok: true; byEpoch: Map<number, Asteroid[]>; missing?: number } | { ok: false; error: string };

/** One request per distinct frame date, a few at a time. Fails only if every request fails. */
export async function fetchAsteroids(
  p: { ra: number; dec: number; fov_arcmin: number },
  epochsMjd: number[],
  fetchFn: typeof fetch = (...a) => fetch(...a),
  signal?: AbortSignal,
): Promise<AsteroidResult> {
  const keys = [...new Set(epochsMjd.map(epochKey))];
  const radiusArcsec = Math.min(36000, Math.ceil(p.fov_arcmin * 60 * 0.75));   // covers the corners
  const byEpoch = new Map<number, Asteroid[]>();
  let failed = 0;
  let next = 0;
  let lastWhy = "";
  async function worker() {
    while (next < keys.length) {
      const k = keys[next++];
      const q = new URLSearchParams({
        "-ep": String(k + 2400000.5), "-ra": String(p.ra), "-dec": String(p.dec), "-rs": String(radiusArcsec),
        "-mime": "json", "-output": "basic", "-observer": "500", "-from": "Tombaugh",
      });
      let done = false;
      for (let attempt = 0; attempt < 2 && !done; attempt++) {
        try {
          if (attempt > 0) await new Promise((r) => setTimeout(r, 1500));
          const res = await fetchFn(`${ENDPOINT}?${q}`, { signal });
          if (!res.ok) { lastWhy = `HTTP ${res.status}`; continue; }
          // an empty field may come back as an empty body or a plain "nothing found" line, not "[]"
          const text = (await res.text()).trim();
          let body: unknown = [];
          if (text.startsWith("{") || text.startsWith("[")) body = JSON.parse(text);
          byEpoch.set(k, parseSkybot(body));
          done = true;
        } catch (e) {
          lastWhy = e instanceof TypeError ? "the browser could not connect, or the service does not allow requests from this page"
            : "its answer could not be read";
        }
      }
      if (!done) failed++;
    }
  }
  await Promise.all([worker(), worker()]);
  if (keys.length > 0 && failed === keys.length) {
    return { ok: false, error: `The asteroid service (IMCCE SkyBoT) did not answer (${lastWhy}). It may be blocked on this network.` };
  }
  return { ok: true, byEpoch, missing: failed };
}

/** Position on the picture as fractions of its width and height (v = 0 is the top; north up, east left). */
export function projectToPicture(a: { ra: number; dec: number }, ra0: number, dec0: number, fovArcmin: number): { u: number; v: number } {
  return skyToPicture(a.ra, a.dec, { ra: ra0, dec: dec0, size: pictureSizePx(fovArcmin) });
}

/** The asteroids drawn for a frame at MJD t, brightest first. */
export function asteroidsAt(byEpoch: Map<number, Asteroid[]>, t: number): Asteroid[] {
  return (byEpoch.get(epochKey(t)) ?? []).slice().sort((a, b) => (a.vmag ?? 99) - (b.vmag ?? 99));
}

/** How many to draw before the magnitude cut starts to matter. */
export const MANY = 20;

/** The magnitude limit that shows everything when there are few, and the brightest MANY otherwise. */
export function defaultLimit(all: Asteroid[]): number {
  const names = new Map<string, number>();
  for (const a of all) names.set(a.name, Math.min(names.get(a.name) ?? 99, a.vmag ?? 99));
  const mags = [...names.values()].sort((x, y) => x - y);
  return mags.length <= MANY ? 30 : Math.max(10, Math.ceil(mags[MANY - 1]));
}

export interface TrackPoint { t: number; ra: number; dec: number }

/** Where one named object was at each frame date it was found, oldest first. */
export function trackFor(name: string, byEpoch: Map<number, Asteroid[]>): TrackPoint[] {
  const out: TrackPoint[] = [];
  for (const [t, rows] of byEpoch) {
    const a = rows.find((r) => r.name === name);
    if (a) out.push({ t, ra: a.ra, dec: a.dec });
  }
  return out.sort((x, y) => x.t - y.t);
}

/** Position at time t: a straight line between the two nearest known dates (a main-belt object
 *  moves almost straight over a day); before the first or after the last, that point itself. */
export function positionAt(track: TrackPoint[], t: number): { ra: number; dec: number } | null {
  if (track.length === 0) return null;
  if (t <= track[0].t) return track[0];
  const last = track[track.length - 1];
  if (t >= last.t) return last;
  for (let i = 1; i < track.length; i++) {
    const a = track[i - 1], b = track[i];
    if (t <= b.t) {
      const f = (t - a.t) / (b.t - a.t);
      const dra = ((b.ra - a.ra + 540) % 360) - 180;   // across RA 0 or 360
      return { ra: (a.ra + dra * f + 360) % 360, dec: a.dec + (b.dec - a.dec) * f };
    }
  }
  return last;
}

/** Names with their brightest magnitude, brightest first, for a pick list. */
export function namesByBrightness(byEpoch: Map<number, Asteroid[]>): { name: string; vmag: number | null }[] {
  const best = new Map<string, number | null>();
  for (const rows of byEpoch.values()) {
    for (const a of rows) {
      const cur = best.get(a.name);
      if (cur === undefined || (a.vmag != null && (cur == null || a.vmag < cur))) best.set(a.name, a.vmag);
    }
  }
  return [...best].map(([name, vmag]) => ({ name, vmag })).sort((x, y) => (x.vmag ?? 99) - (y.vmag ?? 99));
}
