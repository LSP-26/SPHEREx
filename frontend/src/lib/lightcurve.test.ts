import { describe, expect, it, vi } from "vitest";
import { detectorColour, fetchLightcurve, linearScale, measureHere, logScale, matchLightcurve, plottable, separationArcmin, wavelengthColour } from "./lightcurve";

describe("matchLightcurve", () => {
  it("matches a field centred on R Draconis or RW Aurigae, and nothing else", () => {
    expect(matchLightcurve(248.1672, 66.755)?.key).toBe("r_dra");
    expect(matchLightcurve(76.96, 30.40)?.key).toBe("rw_aur");
    expect(matchLightcurve(269.4462, 4.7687)).toBeNull();           // Barnard's Star
    expect(matchLightcurve(248.1672, 66.755 + 5 / 60)).toBeNull();   // 5 arcmin away
  });
  it("measures separation in arcminutes, with RA shrunk by cos(dec)", () => {
    expect(separationArcmin(10, 0, 10, 1)).toBeCloseTo(60);
    expect(separationArcmin(10, 60, 11, 60)).toBeCloseTo(30, 0);
  });
});

describe("plottable", () => {
  it("keeps only points with a positive flux", () => {
    const pts = [{ flux_jy: 1 }, { flux_jy: null }, { flux_jy: 0 }, { flux_jy: -2 }, { flux_jy: 3 }];
    expect(plottable(pts).map((p) => p.flux_jy)).toEqual([1, 3]);
  });
});

describe("scales", () => {
  it("log scale puts the larger flux higher up the page (smaller y)", () => {
    const s = logScale(1, 100, 200);
    expect(s.to(100)).toBeLessThan(s.to(1));
    expect(s.ticks).toContain(10);
    expect(s.to(10)).toBeGreaterThan(s.to(100));
  });
  it("linear scale covers the data with round ticks", () => {
    const s = linearScale(3, 97, 100);
    expect(s.lo).toBeLessThanOrEqual(3);
    expect(s.hi).toBeGreaterThanOrEqual(97);
    expect(s.to(s.lo)).toBe(0);
    expect(s.to(s.hi)).toBeCloseTo(100);
  });
  it("colours short wavelengths blue and long ones red", () => {
    expect(wavelengthColour(0.75)).toContain("hsl(240");
    expect(wavelengthColour(5)).toContain("hsl(0");
  });
});

describe("detectorColour", () => {
  it("gives each of the six detectors its own colour", () => {
    expect(new Set([1, 2, 3, 4, 5, 6].map(detectorColour)).size).toBe(6);
  });
});

describe("fetchLightcurve", () => {
  it("returns the file, and a message when it is missing or broken", async () => {
    const ok = (async () => ({ ok: true, json: async () => ({ points: [], spectrum: [] }) })) as unknown as typeof fetch;
    expect((await fetchLightcurve("r_dra", ok)).ok).toBe(true);
    const missing = (async () => ({ ok: false, json: async () => ({}) })) as unknown as typeof fetch;
    expect((await fetchLightcurve("r_dra", missing)).ok).toBe(false);
    const down = (async () => { throw new TypeError("x"); }) as unknown as typeof fetch;
    expect((await fetchLightcurve("r_dra", down)).ok).toBe(false);
  });
});

describe("measureHere", () => {
  const reply = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body });
  const lc = { key: "here", name: "x", points: [], spectrum: [] };

  it("polls until the server says done and reports progress", async () => {
    const answers = [reply(200, { status: "running", done: 0, total: 0 }), reply(200, { status: "running", done: 5, total: 10 }),
      reply(200, { status: "done", result: lc })];
    const fetchFn = vi.fn(async () => answers.shift()) as unknown as typeof fetch;
    const seen: [number, number][] = [];
    const r = await measureHere({ ra: 10, dec: -5 }, { fetchFn, sleep: async () => {}, onProgress: (d, t) => seen.push([d, t]) });
    expect(r).toEqual({ ok: true, data: lc });
    expect(seen).toEqual([[0, 0], [5, 10]]);
    expect(String((fetchFn as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0])).toBe("/measure?ra=10&dec=-5");
  });

  it("passes on the server's message when it fails, and says so when the server cannot be reached", async () => {
    const bad = vi.fn(async () => reply(200, { status: "error", message: "No SPHEREx frame covers this position." })) as unknown as typeof fetch;
    expect(await measureHere({ ra: 1, dec: 1 }, { fetchFn: bad, sleep: async () => {} })).toEqual({ ok: false, error: "No SPHEREx frame covers this position." });
    const down = vi.fn(async () => { throw new TypeError("network"); }) as unknown as typeof fetch;
    const r = await measureHere({ ra: 1, dec: 1 }, { fetchFn: down });
    expect(r.ok).toBe(false);
    const old = vi.fn(async () => reply(404, {})) as unknown as typeof fetch;
    expect(await measureHere({ ra: 1, dec: 1 }, { fetchFn: old })).toMatchObject({ ok: false, error: expect.stringContaining("Restart") });
  });
});
