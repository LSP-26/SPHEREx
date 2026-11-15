// One search box: coordinates or a name.
//   "269.45 4.69", "269.45, +4.69", "17h57m48.5s +04d41m36s", "17 57 48.5 +04 41 36" -> coordinates
//   anything else -> a name, looked up with SIMBAD and moved to today by its proper motion.
import { parseDec, parseRa } from "./coords";
import { resolveName } from "./nameLookup";

export type Found =
  | { ok: true; ra: number; dec: number; name: string | null; note?: string }
  | { ok: false; error: string };

/** Splits typed coordinates into an RA part and a Dec part, or null if it does not look like coordinates. */
export function splitCoords(text: string): [string, string] | null {
  const t = text.trim();
  if (!/^[\d\s.,:+\-hdms°'"′″−]+$/i.test(t) || !/\d/.test(t)) return null;
  const clean = t.replace(/−/g, "-");
  if (clean.includes(",")) {
    const [a, b, ...rest] = clean.split(",");
    return rest.length === 0 && b !== undefined ? [a.trim(), b.trim()] : null;
  }
  // a sign starts the Dec ("... +04 41 36")
  const signed = clean.search(/\s[+-]/);
  if (signed > 0) return [clean.slice(0, signed).trim(), clean.slice(signed).trim()];
  const parts = clean.split(/\s+/);
  if (parts.length === 2) return [parts[0], parts[1]];
  if (parts.length === 6) return [parts.slice(0, 3).join(" "), parts.slice(3).join(" ")];
  return null;
}

export function parseCoords(text: string): Found | null {
  const pair = splitCoords(text);
  if (!pair) return null;
  const ra = parseRa(pair[0]);
  const dec = parseDec(pair[1]);
  if (!ra.ok) return { ok: false, error: ra.error };
  if (!dec.ok) return { ok: false, error: dec.error };
  return { ok: true, ra: ra.value, dec: dec.value, name: null };
}

/** The epoch positions are moved to: SPHEREx's survey is 2025 to 2027. */
export const EPOCH_YEAR = 2026.0;

/** Moves a J2000 position by its proper motion (mas/yr, pmra includes cos dec) to `year`. */
export function propagate(ra: number, dec: number, pmra: number, pmdec: number, year = EPOCH_YEAR) {
  const dt = year - 2000;
  const ddec = (pmdec * dt) / 3.6e6;
  const dra = (pmra * dt) / 3.6e6 / Math.cos((dec * Math.PI) / 180);
  return { ra: (((ra + dra) % 360) + 360) % 360, dec: dec + ddec };
}

export async function search(text: string, fetchFn: typeof fetch = (...a) => fetch(...a)): Promise<Found> {
  const t = text.trim();
  if (!t) return { ok: false, error: "Type a star's name or its coordinates." };
  const coords = parseCoords(t);
  if (coords) return coords;
  const r = await resolveName(t, fetchFn);
  if (!r.ok) return { ok: false, error: r.error };
  if (r.pmra != null && r.pmdec != null && Math.hypot(r.pmra, r.pmdec) > 50) {
    const p = propagate(r.ra, r.dec, r.pmra, r.pmdec);
    const moved = Math.hypot(r.pmra, r.pmdec) * (EPOCH_YEAR - 2000) / 1000;
    return { ok: true, ...p, name: r.name, note: `Moved ${moved.toFixed(0)}″ from its year-2000 position to where it is now.` };
  }
  return { ok: true, ra: r.ra, dec: r.dec, name: r.name };
}
