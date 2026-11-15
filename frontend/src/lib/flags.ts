// Where the SPHEREx pipeline flagged pixels in one frame (GET /flags, backend/pipeline/flagmap.py):
// an RGBA layer laid out like the frame's picture, and the fraction of the field in each group.
// The picture shows flagged pixels by their value; measurements leave them out.

export interface FlagCategory {
  key: string;
  label: string;
  colour: string;
  drawn: boolean;
  bits: string[];
  fraction: number;
}

export interface FlagMap {
  png_url: string;
  frame_id: string;
  width: number;
  height: number;
  categories: FlagCategory[];
  bits: Record<string, number>;
}

export type FlagFetch = { ok: true; data: FlagMap } | { ok: false; error: string };

export interface FlagTarget {
  ra: number; dec: number; fov_arcmin: number; orient: "north" | "detector";
  /** A replay: the recorded layers (flags.json) are read instead of asking the server. */
  staticUrl?: string;
}

const recorded = new Map<string, Promise<{ frames: Record<string, FlagMap> } | null>>();

async function fetchRecordedFlags(
  t: FlagTarget & { staticUrl: string }, frameId: string, fetchFn: typeof fetch,
): Promise<FlagFetch> {
  let file = recorded.get(t.staticUrl);
  if (!file) {
    file = fetchFn(t.staticUrl).then(async (r) => (r.ok ? ((await r.json()) as { frames: Record<string, FlagMap> }) : null)).catch(() => null);
    recorded.set(t.staticUrl, file);
  }
  const body = await file;
  if (!body) { recorded.delete(t.staticUrl); return { ok: false, error: "The recorded flag layers could not be loaded." }; }
  const m = body.frames?.[frameId];
  if (!m) return { ok: false, error: "No flag layer was recorded for this frame." };
  const dir = t.staticUrl.slice(0, t.staticUrl.lastIndexOf("/") + 1);
  return { ok: true, data: { ...m, png_url: dir + m.png_url } };
}

/** Only single catalogue frames have flags: not refined co-adds, not the merged colour picture. */
export const hasFlags = (s: { stage: string; label: { detector: number } }): boolean =>
  s.stage === "preview" && s.label.detector !== 0;

export async function fetchFlags(
  t: FlagTarget,
  frameId: string,
  apiBase = "",
  fetchFn: typeof fetch = (...x) => fetch(...x),
): Promise<FlagFetch> {
  const q = new URLSearchParams({ ra: String(t.ra), dec: String(t.dec), fov: String(t.fov_arcmin), frame: frameId });
  if (t.orient === "detector") q.set("orient", "detector");
  if (t.staticUrl) return fetchRecordedFlags({ ...t, staticUrl: t.staticUrl }, frameId, fetchFn);
  try {
    const res = await fetchFn(`${apiBase}/flags?${q}`);
    if (!res.ok) {
      let why = "";
      try { why = ((await res.json()) as { message?: string }).message ?? ""; } catch { /* no body */ }
      if (res.status === 404 && !why) return { ok: false, error: "This server does not have the flag map yet. Restart it after updating." };
      return { ok: false, error: why || `The flags could not be read (HTTP ${res.status}).` };
    }
    const body = (await res.json()) as Partial<FlagMap>;
    if (!body || typeof body.png_url !== "string" || !Array.isArray(body.categories)) {
      return { ok: false, error: "The server's answer could not be read." };
    }
    return { ok: true, data: { ...(body as FlagMap), png_url: apiBase + body.png_url } };
  } catch {
    return { ok: false, error: "The flags need the live server, which could not be reached." };
  }
}
