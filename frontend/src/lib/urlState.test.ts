import { describe, expect, it } from "vitest";
import { DEFAULTS, fromQuery, toQuery, withDefaults, type FlipbookParams } from "./urlState";

// Every `query` below was produced by contracts/url_state.py (to_query on the same request),
// so these tests fail if the TypeScript and Python versions ever disagree.
const GOLDEN: { params: Partial<FlipbookParams> & { ra: number; dec: number }; query: string }[] = [
  { params: { ra: 133.7951, dec: -7.2452 }, query: "ra=133.7951&dec=-7.2452" },
  {
    params: { ra: 269.452, dec: 4.693, fov_arcmin: 12.5, view: "mover", detector: 3, use_zodi: false, preview_cap: 24 },
    query: "ra=269.452&dec=4.693&fov=12.5&view=mover&det=3&cap=24&zodi=0",
  },
  { params: { ra: 0, dec: 0 }, query: "ra=0&dec=0" },
  { params: { ra: 359.999999, dec: -90, fov_arcmin: 120 }, query: "ra=359.999999&dec=-90&fov=120" },
  {
    params: { ra: 10.5, dec: 89.123456789, lambda_min_um: 0.75, lambda_max_um: 5, release: "QR3" },
    query: "ra=10.5&dec=89.123457&lmin=0.75&lmax=5&release=QR3",
  },
  {
    params: { ra: 100, dec: -0.000001, fov_arcmin: 0.1, detector: 6, preview_cap: 200, use_zodi: true },
    query: "ra=100&dec=-0.000001&fov=0.1&det=6&cap=200",
  },
  { params: { ra: 10, dec: 0, orient: "detector" }, query: "ra=10&dec=0&orient=detector" },
  { params: { ra: 10, dec: 0, fill: false }, query: "ra=10&dec=0&fill=0" },
  { params: { ra: 133.795129, dec: -7.245147, fov_arcmin: 2.25 }, query: "ra=133.795129&dec=-7.245147&fov=2.25" },
];

describe("toQuery matches the Python implementation", () => {
  for (const { params, query } of GOLDEN) {
    it(query, () => expect(toQuery(withDefaults(params))).toBe(query));
  }
});

describe("fromQuery", () => {
  it("round-trips every golden case", () => {
    for (const { query } of GOLDEN) {
      const r = fromQuery(query);
      expect(r.ok).toBe(true);
      if (r.ok) expect(toQuery(r.params)).toBe(query);
    }
  });

  it("fills defaults, accepts a leading ?, and ignores keys it does not know", () => {
    const r = fromQuery("?ra=10&dec=-5&utm_source=somewhere");
    expect(r).toEqual({ ok: true, params: { ...DEFAULTS, ra: 10, dec: -5 } });
  });

  it("reads zodi in the spellings Python accepts", () => {
    for (const [raw, want] of [["1", true], ["true", true], ["True", true], ["0", false], ["false", false]] as const) {
      const r = fromQuery(`ra=1&dec=1&zodi=${raw}`);
      expect(r.ok && r.params.use_zodi).toBe(want);
    }
  });

  it("reports every problem it finds, in plain words", () => {
    const r = fromQuery("ra=400&dec=95&fov=0&view=sideways&det=7&cap=0&lmin=-1&zodi=maybe");
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.errors).toEqual(expect.arrayContaining([
        "ra must be from 0 up to 360",
        "dec must be from -90 to 90",
        "fov must be above 0 and at most 120",
        "view must be sky, mover or spectral",
        "det must be a whole number from 1 to 6",
        "cap must be a whole number from 1 to 200",
        "lmin must be above 0",
        "zodi must be 0 or 1",
      ]));
    }
  });

  it("requires ra and dec, and rejects text where numbers belong", () => {
    expect(fromQuery("")).toEqual({ ok: false, errors: ["ra is required", "dec is required"] });
    const r = fromQuery("ra=abc&dec=1");
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.errors).toContain("ra must be a number");
  });
});
