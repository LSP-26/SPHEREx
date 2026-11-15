// How frames are grouped for blinking.
//
// SPHEREx has six detector arrays, each covering its own slice of the near-infrared. A single
// frame records the sky at one wavelength, and the same spot is seen at about the same wavelength
// by the same detector on every pass over the sky. So the honest way to blink is one detector
// (one "band") at a time: every look SPHEREx took of the spot with that detector, in time order.
// Then stars that do not move stay put and anything that moves or changes stands out.
import type { Slot } from "./reducer";

export interface Band {
  detector: number;
  /** Approximate wavelength range of the detector, µm. */
  range: [number, number];
}

export const BANDS: Band[] = [
  { detector: 1, range: [0.75, 1.1] },
  { detector: 2, range: [1.1, 1.62] },
  { detector: 3, range: [1.62, 2.42] },
  { detector: 4, range: [2.42, 3.82] },
  { detector: 5, range: [3.82, 4.42] },
  { detector: 6, range: [4.42, 5.0] },
];

/** The detector of a merged colour frame from the server's refine stage. */
export const COLOUR_DETECTOR = 0;

export interface Look {
  slot: number;
  frame: Slot;
  t: number;
  lambda: number;
}

/** Every frame of each detector, oldest first. Detector 0 (server-made colour) is left out. */
export function looksByBand(slots: (Slot | null)[]): Map<number, Look[]> {
  const out = new Map<number, Look[]>();
  slots.forEach((s, slot) => {
    if (!s || s.label.detector === COLOUR_DETECTOR) return;
    const list = out.get(s.label.detector) ?? [];
    list.push({ slot, frame: s, t: s.label.t_mid_mjd, lambda: s.label.lambda_um });
    out.set(s.label.detector, list);
  });
  for (const list of out.values()) list.sort((a, b) => a.t - b.t);
  return out;
}

/** Preference when bands tie: the sharp short-wavelength bands first, the dichroic band (D4) last. */
const PREFERENCE = [2, 1, 3, 5, 6, 4];

/** The band to open on: the one with the most looks (a blink needs at least two). */
export function defaultBand(byBand: Map<number, Look[]>): number | null {
  let best: number | null = null;
  for (const d of PREFERENCE) {
    const n = byBand.get(d)?.length ?? 0;
    if (n === 0) continue;
    if (best === null || n > (byBand.get(best)?.length ?? 0)) best = d;
  }
  return best;
}

/** Median wavelength of a band's looks, for its label. */
export function bandWavelength(looks: Look[]): number | null {
  if (looks.length === 0) return null;
  const sorted = looks.map((l) => l.lambda).sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

// ---- colour: one picture per pass over the sky ---------------------------------------------

/** Frames further apart than this belong to different passes. SPHEREx maps the whole sky every
 *  six months; a spot is seen for a few days to a few weeks on each pass. */
export const PASS_GAP_DAYS = 45;

/** Which detectors feed each colour: blue = short wavelengths, red = long. */
export const COLOUR_CHANNELS = { b: [1, 2], g: [3, 4], r: [5, 6] } as const;

export interface Pass {
  start: number;
  end: number;
  /** The first frame of each detector in this pass. */
  byDetector: Map<number, Look>;
}

/** Splits all frames into passes by gaps in time. */
export function passes(slots: (Slot | null)[]): Pass[] {
  const all = [...looksByBand(slots).values()].flat().sort((a, b) => a.t - b.t);
  const out: Pass[] = [];
  for (const look of all) {
    const cur = out[out.length - 1];
    if (!cur || look.t - cur.end > PASS_GAP_DAYS) {
      out.push({ start: look.t, end: look.t, byDetector: new Map([[look.frame.label.detector, look]]) });
    } else {
      cur.end = look.t;
      if (!cur.byDetector.has(look.frame.label.detector)) cur.byDetector.set(look.frame.label.detector, look);
    }
  }
  return out;
}

/** The colour channels a pass can fill (a channel needs at least one of its detectors). */
export function channelsOf(p: Pass): ("r" | "g" | "b")[] {
  return (["r", "g", "b"] as const).filter((c) => COLOUR_CHANNELS[c].some((d) => p.byDetector.has(d)));
}

/** Passes that can make a colour picture: at least two of the three channels. */
export function colourPasses(slots: (Slot | null)[]): Pass[] {
  return passes(slots).filter((p) => channelsOf(p).length >= 2);
}

/** "Sep 2025" */
export function monthYear(mjd: number): string {
  const d = new Date((mjd - 40587) * 86_400_000);
  return d.toLocaleString("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });
}

/** "12 Sep 2025" */
export function dayMonthYear(mjd: number): string {
  const d = new Date((mjd - 40587) * 86_400_000);
  return d.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

/** Years between two dates given in MJD. */
export const yearsBetween = (a: number, b: number) => Math.abs(b - a) / 365.25;
