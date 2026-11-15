// What the Gaia DR3 stars on the picture tell a person: which ones move fast, how far away a star
// is, and whether a spot on the picture is a known star at all.
import { starAt, type OverlayData, type OverlayStar } from "./overlay";

/** Faster than this, a star's motion can show between SPHEREx looks over a year or two (a pixel is 6.15″). */
export const FAST_ARCSEC_YR = 0.5;

/** Total proper motion in arcsec per year, or null when Gaia has none. */
export function motionArcsecYr(s: OverlayStar): number | null {
  return s.pmra == null || s.pmdec == null ? null : Math.hypot(s.pmra, s.pmdec) / 1000;
}

/** Distance in light-years from the parallax, only when it is measured well enough to mean something. */
export function distanceLy(s: OverlayStar): number | null {
  if (s.parallax == null || !(s.parallax > 1)) return null;
  return 3261.56 / s.parallax;
}

/** Stars moving at least `minArcsecYr`, fastest first. These are the ones a blink can show. */
export function fastMovers(data: OverlayData, minArcsecYr = FAST_ARCSEC_YR): OverlayStar[] {
  return data.stars
    .filter((s) => (motionArcsecYr(s) ?? 0) >= minArcsecYr)
    .sort((a, b) => (motionArcsecYr(b) ?? 0) - (motionArcsecYr(a) ?? 0));
}

/** The Gaia star nearest to (u, v) at time t, if one lies within `maxPx` picture pixels. */
export function nearestStar(
  data: OverlayData, u: number, v: number, t: number, maxPx = 1.5,
): { star: OverlayStar; px: number } | null {
  let best: { star: OverlayStar; px: number } | null = null;
  for (const s of data.stars) {
    const p = starAt(s, data, t);
    const px = Math.hypot(p.u - u, p.v - v) * data.size;
    if (px <= maxPx && (!best || px < best.px || (px === best.px && (s.g_mag ?? 99) < (best.star.g_mag ?? 99)))) {
      best = { star: s, px };
    }
  }
  return best;
}

/** "moves 10.4″ a year · 5.96 light-years away · G 8.2" */
export function describeStar(s: OverlayStar): string {
  const parts: string[] = [];
  const pm = motionArcsecYr(s);
  if (pm != null) parts.push(pm >= 0.01 ? `moves ${pm.toFixed(pm < 1 ? 2 : 1)}″ a year` : "barely moves");
  const d = distanceLy(s);
  if (d != null) parts.push(`${d < 100 ? d.toFixed(1) : Math.round(d).toLocaleString("en-US")} light-years away`);
  if (s.g_mag != null) parts.push(`G ${s.g_mag.toFixed(1)}`);
  return parts.join(" · ");
}
