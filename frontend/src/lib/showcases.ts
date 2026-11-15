// The ready-made examples. backend/offline/build_showcases.py writes manifest.json and one
// events.json per showcase under /showcases/; the page replays them (?ex=<key>).
import { toQuery, withDefaults } from "./urlState";

export interface Showcase {
  key: string;
  name: string;
  ra: number;
  dec: number;
  fov_arcmin: number;
  n_frames: number;
  elapsed_s: number;
  events: string; // path under /showcases/
  thumb: string; // path inside the showcase folder
  note: string;
  /** Path under /showcases/ of the recorded flag layers (backend/offline/build_flags.py). */
  flags?: string;
}

export const SHOWCASE_BASE = `${import.meta.env.BASE_URL ?? "/"}showcases/`;

function isShowcase(x: unknown): x is Showcase {
  if (typeof x !== "object" || x === null) return false;
  const s = x as Record<string, unknown>;
  return (
    typeof s.key === "string" && typeof s.name === "string" && typeof s.ra === "number" &&
    typeof s.dec === "number" && typeof s.fov_arcmin === "number" && typeof s.n_frames === "number" &&
    typeof s.events === "string" && typeof s.thumb === "string"
  );
}

/** The showcases in a manifest body, skipping any entry that is not the expected shape. */
export function parseManifest(body: unknown): Showcase[] {
  const list = (body as { showcases?: unknown } | null)?.showcases;
  return Array.isArray(list) ? list.filter(isShowcase) : [];
}

export const eventsSrc = (s: Showcase) => SHOWCASE_BASE + s.events;
export const liveHref = (s: Showcase) =>
  `?${toQuery(withDefaults({ ra: s.ra, dec: s.dec, fov_arcmin: s.fov_arcmin }))}`;
export const thumbSrc = (s: Showcase) => `${SHOWCASE_BASE}${s.key}/${s.thumb}`;

/** Where backend/offline/build_overlays.py saves a showcase's Gaia stars (next to events.json). */
export const overlaySrc = (s: Showcase) => `${SHOWCASE_BASE}${s.key}/overlay.json`;

/** The saved flag layers of a showcase, if it has them. */
export const flagsSrc = (s: Showcase) => (s.flags ? SHOWCASE_BASE + s.flags : undefined);

/** The showcase a ?static= replay URL plays, if it is one of ours. */
export function showcaseFor(staticUrl: string | null, list: Showcase[]): Showcase | undefined {
  if (!staticUrl) return undefined;
  return list.find((s) => SHOWCASE_BASE + s.events === staticUrl);
}
