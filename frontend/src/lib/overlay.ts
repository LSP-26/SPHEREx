// Gaia stars drawn over the flipbook picture. The server (GET /overlay) projects them onto the
// same grid as the pictures: u and v are the position as a fraction of the picture's width and
// height (v = 0 is the top), at epoch_mjd, and vx and vy are px per year. A star at frame time t
// is at x + vx * (t - epoch_mjd) / 365.25, so moving stars drift across the flipbook.

export interface OverlayStar {
  source_id: number | null;
  x: number;
  y: number;
  u: number;
  v: number;
  vx: number;
  vy: number;
  g_mag: number | null;
  pmra: number | null;
  pmdec: number | null;
  parallax: number | null;
}

export interface OverlayData {
  size: number;
  epoch_mjd: number;
  pixel_arcsec: number;
  stars: OverlayStar[];
}

export type OverlayResult = { ok: true; data: OverlayData } | { ok: false; error: string };

/** Stars for the picture. `p.staticUrl` (a showcase replay) reads the saved overlay.json instead
 *  of asking the server, so the stars work with the server off. */
export async function fetchOverlay(
  p: { ra: number; dec: number; fov_arcmin: number; staticUrl?: string },
  apiBase = "",
  fetchFn: typeof fetch = (...a) => fetch(...a),
  signal?: AbortSignal,
): Promise<OverlayResult> {
  const q = new URLSearchParams({ ra: String(p.ra), dec: String(p.dec), fov: String(p.fov_arcmin) });
  try {
    const res = await fetchFn(p.staticUrl ?? `${apiBase}/overlay?${q}`, { signal });
    if (p.staticUrl && !res.ok) return { ok: false, error: "No Gaia stars were saved for this example." };
    if (res.status === 503) return { ok: false, error: "The star catalogue could not be reached from the server. Try again later." };
    if (!res.ok) {
      let why = "";
      try { why = ((await res.json()) as { message?: string }).message ?? ""; } catch { /* no body */ }
      return { ok: false, error: `The star overlay is not available (server said HTTP ${res.status}${why ? `: ${why}` : ""}).` };
    }
    const body = (await res.json()) as Partial<OverlayData>;
    if (!body || !Array.isArray(body.stars) || typeof body.size !== "number" || typeof body.epoch_mjd !== "number") {
      return { ok: false, error: "The star overlay is not available." };
    }
    return { ok: true, data: body as OverlayData };
  } catch {
    return { ok: false, error: "The star overlay is not available without the live server." };
  }
}

/** Position of a star on the picture, as a fraction of its width and height, at MJD `t`. */
export function starAt(s: OverlayStar, data: OverlayData, t: number): { u: number; v: number } {
  const years = (t - data.epoch_mjd) / 365.25;
  return { u: (s.x + s.vx * years + 0.5) / data.size, v: (s.y + s.vy * years + 0.5) / data.size };
}
