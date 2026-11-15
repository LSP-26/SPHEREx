// Turning per-frame measurements into curves a person can read.
//
// A star near the ecliptic poles can have hundreds of frames per detector, many within a few hours
// of each other. Drawn one dot per frame they pile up into an unreadable smear, so frames of one
// detector are grouped into visits (frames less than VISIT_GAP_DAYS apart) and each visit is drawn
// as its median with the full range as a bar.
import type { LcPoint, SpectrumPoint } from "./lightcurve";

export const VISIT_GAP_DAYS = 1.5;

export interface VisitBin {
  t: number;
  flux: number;
  lo: number;
  hi: number;
  n: number;
  /** Most frames were saturated: the true brightness is at least `flux`. */
  floor: boolean;
  lambda: number;
}

function median(xs: number[]): number {
  const s = xs.slice().sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** One detector's frames grouped into visits, oldest first. Points without a positive flux are skipped. */
export function binByVisit(points: LcPoint[], gapDays = VISIT_GAP_DAYS): VisitBin[] {
  const pts = points.filter((p) => p.flux_jy != null && p.flux_jy > 0).sort((a, b) => a.t_mjd - b.t_mjd);
  const groups: LcPoint[][] = [];
  for (const p of pts) {
    const g = groups[groups.length - 1];
    if (g && p.t_mjd - g[g.length - 1].t_mjd <= gapDays) g.push(p);
    else groups.push([p]);
  }
  return groups.map((g) => {
    const f = g.map((p) => p.flux_jy as number);
    return {
      t: median(g.map((p) => p.t_mjd)),
      flux: median(f),
      lo: Math.min(...f),
      hi: Math.max(...f),
      n: g.length,
      floor: g.filter((p) => p.lower_limit).length * 2 > g.length,
      lambda: median(g.map((p) => p.lambda_um)),
    };
  });
}

/** The detector with the most measured frames, for when none is chosen. */
export function busiestDetector(points: LcPoint[]): number | null {
  const n = new Map<number, number>();
  for (const p of points) if (p.flux_jy != null && p.flux_jy > 0) n.set(p.detector, (n.get(p.detector) ?? 0) + 1);
  let best: number | null = null;
  for (const [d, k] of n) if (best === null || k > (n.get(best) ?? 0)) best = d;
  return best;
}

export function spectrumPoints(spec: SpectrumPoint[]): (SpectrumPoint & { flux_jy: number })[] {
  return spec
    .filter((p): p is SpectrumPoint & { flux_jy: number } => p.flux_jy != null && p.flux_jy > 0)
    .sort((a, b) => a.lambda_um - b.lambda_um);
}

/** Where common molecules and atoms leave their mark in SPHEREx's range. A dip (absorption) or bump
 *  (emission) at one of these suggests that ingredient; they are guides for the eye, not a fit. */
export interface Feature {
  um: number;
  label: string;
  note: string;
}

export const FEATURES: Feature[] = [
  { um: 1.4, label: "H₂O", note: "water vapour: absorbs in cool stars and brown dwarfs" },
  { um: 1.9, label: "H₂O", note: "water vapour: a second, deeper band" },
  { um: 2.166, label: "Brγ", note: "hydrogen: emission from hot gas, e.g. gas falling onto young stars" },
  { um: 2.3, label: "CO", note: "carbon monoxide: strong in cool giant stars such as Miras" },
  { um: 3.05, label: "H₂O ice", note: "water ice: in cold dust clouds and on comets" },
  { um: 3.3, label: "CH₄", note: "methane: marks cool (T and Y type) brown dwarfs" },
  { um: 4.27, label: "CO₂", note: "carbon dioxide ice: a key SPHEREx ice band" },
  { um: 4.67, label: "CO", note: "carbon monoxide gas or ice" },
];
