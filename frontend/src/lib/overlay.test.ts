import { describe, expect, it, vi } from "vitest";
import { fetchOverlay, starAt, type OverlayData, type OverlayStar } from "./overlay";

const star: OverlayStar = { source_id: 1, x: 10, y: 20, u: 0.1, v: 0.2, vx: 1, vy: -2, g_mag: 9, pmra: 1, pmdec: 2, parallax: 3 };
const data: OverlayData = { size: 100, epoch_mjd: 60000, pixel_arcsec: 6.15, stars: [star] };
const params = { ra: 269.45, dec: 4.69, fov_arcmin: 5 };

describe("starAt", () => {
  it("is at its own position at the epoch", () => {
    expect(starAt(star, data, 60000)).toEqual({ u: 0.105, v: 0.205 });
  });
  it("moves by its velocity: a year later is vx, vy pixels away", () => {
    const p = starAt(star, data, 60000 + 365.25);
    expect(p.u).toBeCloseTo(0.115);
    expect(p.v).toBeCloseTo(0.185);
  });
});

describe("fetchOverlay", () => {
  it("asks the server for the position and field", async () => {
    const fetchFn = vi.fn(async (..._a: [string | URL | Request, RequestInit?]) => ({ ok: true, status: 200, json: async () => data }));
    const r = await fetchOverlay(params, "http://localhost:8000", fetchFn as unknown as typeof fetch);
    expect(r).toEqual({ ok: true, data });
    expect(String(fetchFn.mock.calls[0][0])).toBe("http://localhost:8000/overlay?ra=269.45&dec=4.69&fov=5");
  });
  it("explains a 503 (no internet on the server)", async () => {
    const f = (async () => ({ ok: false, status: 503, json: async () => ({}) })) as unknown as typeof fetch;
    const r = await fetchOverlay(params, "", f);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("could not be reached");
  });
  it("copes with a missing server and with nonsense", async () => {
    const down = (async () => { throw new TypeError("fetch failed"); }) as unknown as typeof fetch;
    expect((await fetchOverlay(params, "", down)).ok).toBe(false);
    const junk = (async () => ({ ok: true, status: 200, json: async () => ({ hello: 1 }) })) as unknown as typeof fetch;
    expect((await fetchOverlay(params, "", junk)).ok).toBe(false);
  });
});

describe("fetchOverlay from a saved file", () => {
  it("reads the saved overlay.json instead of the server", async () => {
    const fetchFn = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ size: 10, epoch_mjd: 1, pixel_arcsec: 6, stars: [] }) }));
    const r = await fetchOverlay({ ra: 1, dec: 2, fov_arcmin: 5, staticUrl: "/showcases/x/overlay.json" }, "http://api", fetchFn as unknown as typeof fetch);
    expect(r.ok).toBe(true);
    expect(fetchFn).toHaveBeenCalledWith("/showcases/x/overlay.json", expect.anything());
  });
  it("says so when no stars were saved", async () => {
    const fetchFn = vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) }));
    const r = await fetchOverlay({ ra: 1, dec: 2, fov_arcmin: 5, staticUrl: "/s/overlay.json" }, "", fetchFn as unknown as typeof fetch);
    expect(r).toEqual({ ok: false, error: "No Gaia stars were saved for this example." });
  });
});
