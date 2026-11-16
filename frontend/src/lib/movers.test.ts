import { describe, expect, it, vi } from "vitest";
import { fetchMovers, rateText, trackLabel, type MoversData } from "./movers";

const body: MoversData = {
  ra: 180, dec: 0, fov_arcmin: 10, size: 98, visit: 2,
  visits: [{ index: 2, t_start_mjd: 60900, t_end_mjd: 60902, n_frames: 40, n_bursts: 5 }],
  frames_used: 40, n_detections: 12, known_objects: [], notes: [], skybot_checked: true, png_url: "/frames/abc.png",
  tracks: [
    {
      points: [{ t_mjd: 60900.1, x: 10, y: 20, u: 0.1, v: 0.2, ra: 180, dec: 0, snr: 8, n_frames: 3 }],
      rate_px_h: 6, rate_arcsec_h: 37, pa_deg: 90, rms_px: 0.3, snr: 9, known: "Massalia", status: "known",
    },
  ],
};
const params = { ra: 180, dec: 0, fov_arcmin: 10 };
const reply = (status: number, json: unknown) => vi.fn(async (..._a: [string | URL | Request, RequestInit?]) => ({ ok: status < 400, status, json: async () => json }));

describe("fetchMovers", () => {
  it("asks for the field, and for a visit only when one is chosen", async () => {
    const f = reply(200, body);
    const r = await fetchMovers(params, "http://localhost:8000", f as unknown as typeof fetch);
    expect(r.ok).toBe(true);
    expect(String(f.mock.calls[0][0])).toBe("http://localhost:8000/movers?ra=180&dec=0&fov=10");
    await fetchMovers({ ...params, visit: 3 }, "", f as unknown as typeof fetch);
    expect(String(f.mock.calls[1][0])).toBe("/movers?ra=180&dec=0&fov=10&visit=3");
  });
  it("fills in missing lists so the panel never reads undefined", async () => {
    const { known_objects: _k, notes: _n, ...slim } = body;
    const r = await fetchMovers(params, "", reply(200, slim) as unknown as typeof fetch);
    expect(r.ok && r.data.known_objects).toEqual([]);
    expect(r.ok && r.data.notes).toEqual([]);
  });
  it("says so when no frame covers the position", async () => {
    const r = await fetchMovers(params, "", reply(404, { message: "No SPHEREx frame covers this position." }) as unknown as typeof fetch);
    expect(r).toEqual({ ok: false, error: "No SPHEREx frame covers this position." });
  });
  it("refuses a reply that is not a search result", async () => {
    const r = await fetchMovers(params, "", reply(200, { hello: 1 }) as unknown as typeof fetch);
    expect(r.ok).toBe(false);
  });
  it("reports a server that is off, and a cancelled search", async () => {
    const down = vi.fn(async () => { throw new TypeError("failed to fetch"); });
    expect(await fetchMovers(params, "", down as unknown as typeof fetch)).toEqual({ ok: false, error: "The moving-object search needs the live server." });
    const gone = vi.fn(async () => { throw new DOMException("aborted", "AbortError"); });
    expect(await fetchMovers(params, "", gone as unknown as typeof fetch)).toEqual({ ok: false, error: "Cancelled." });
  });
});

describe("labels", () => {
  it("names a track by its known object, else calls it a candidate", () => {
    expect(trackLabel({ known: "Massalia" })).toBe("Massalia");
    expect(trackLabel({ known: null })).toBe("Candidate");
  });
  it("writes speeds short", () => {
    expect(rateText(37.2)).toBe("37″/h");
    expect(rateText(4.26)).toBe("4.3″/h");
  });
});
