// Moving-object search (GET /movers, backend/jobs/movers.py). The server looks at one visit of a
// field (SPHEREx stares at a spot for one to four days, in bursts of 2 to 4 exposures), keeps
// what shows up in two or more frames of a burst, and links detections that lie on a straight
// line at a steady rate across bursts. Each track is checked against SkyBoT (known asteroids and
// comets): a match gets that name, anything else is only a CANDIDATE.
// u and v are fractions of the returned picture's width and height (v = 0 is the top).

export interface MoverPoint {
  t_mjd: number;
  x: number;
  y: number;
  u: number;
  v: number;
  ra: number;
  dec: number;
  snr: number;
  n_frames: number;
}

export interface MoverTrack {
  points: MoverPoint[];
  rate_px_h: number;
  rate_arcsec_h: number;
  pa_deg: number;
  rms_px: number;
  snr: number;
  known: string | null;
  status: "known" | "candidate";
}

export interface KnownObject {
  name: string;
  vmag: number | null;
  cls: string | null;
  rate_arcsec_h: number;
  bursts_in_field: number;
  bursts_on_track: number;
  tracked: boolean;
}

export interface VisitInfo {
  index: number;
  t_start_mjd: number;
  t_end_mjd: number;
  n_frames: number;
  n_bursts: number;
}

export interface MoversData {
  ra: number;
  dec: number;
  fov_arcmin: number;
  size: number;
  visit: number;
  visits: VisitInfo[];
  frames_used: number;
  n_detections: number;
  tracks: MoverTrack[];
  known_objects: KnownObject[];
  skybot_checked: boolean;
  notes: string[];
  png_url: string;
}

export type MoversResult = { ok: true; data: MoversData } | { ok: false; error: string };

/** The server searches at most this wide a field (backend/jobs/movers.py MAX_FOV_ARCMIN). */
export const MOVERS_MAX_FOV_ARCMIN = 30;

/** Search one visit of the field (the busiest when `visit` is left out). The run can take a
 *  minute: it reads every frame of the visit. */
export async function fetchMovers(
  p: { ra: number; dec: number; fov_arcmin: number; visit?: number },
  apiBase = "",
  fetchFn: typeof fetch = (...a) => fetch(...a),
  signal?: AbortSignal,
): Promise<MoversResult> {
  const q = new URLSearchParams({ ra: String(p.ra), dec: String(p.dec), fov: String(p.fov_arcmin) });
  if (p.visit !== undefined) q.set("visit", String(p.visit));
  try {
    const res = await fetchFn(`${apiBase}/movers?${q}`, { signal });
    if (!res.ok) {
      let why = "";
      try { why = ((await res.json()) as { message?: string }).message ?? ""; } catch { /* no body */ }
      if (res.status === 404) return { ok: false, error: why || "No SPHEREx frame covers this position." };
      if (res.status === 501) return { ok: false, error: "This server cannot search for moving objects." };
      return { ok: false, error: why || `The moving-object search failed (server said HTTP ${res.status}).` };
    }
    const body = (await res.json()) as Partial<MoversData>;
    if (!body || !Array.isArray(body.tracks) || !Array.isArray(body.visits) || typeof body.png_url !== "string") {
      return { ok: false, error: "The moving-object search returned something unexpected." };
    }
    return { ok: true, data: { ...(body as MoversData), known_objects: body.known_objects ?? [], notes: body.notes ?? [] } };
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") return { ok: false, error: "Cancelled." };
    return { ok: false, error: "The moving-object search needs the live server." };
  }
}

/** What to call a track: its SkyBoT name, or "Candidate" when nothing known matches. */
export function trackLabel(t: Pick<MoverTrack, "known">): string {
  return t.known ?? "Candidate";
}

/** Speed in arcseconds per hour as short text, for example "37″/h". */
export function rateText(arcsecPerHour: number): string {
  return `${arcsecPerHour >= 10 ? Math.round(arcsecPerHour) : arcsecPerHour.toFixed(1)}″/h`;
}
