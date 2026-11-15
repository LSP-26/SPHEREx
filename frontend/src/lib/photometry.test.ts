import { describe, expect, it } from "vitest";
import type { LcPoint } from "./lightcurve";
import { FEATURES, binByVisit, busiestDetector, spectrumPoints } from "./photometry";

const pt = (t: number, flux: number | null, detector = 1, lower = false): LcPoint => ({
  frame_id: `f${t}`, detector, t_mjd: t, lambda_um: 1, flux_jy: flux, err_jy: null, lower_limit: lower, reason: null, flags: [],
});

describe("binByVisit", () => {
  it("groups frames close in time into one visit, median and range", () => {
    const bins = binByVisit([pt(100, 1), pt(100.2, 3), pt(100.4, 2), pt(300, 5), pt(300.1, 7)]);
    expect(bins.length).toBe(2);
    expect(bins[0]).toMatchObject({ flux: 2, lo: 1, hi: 3, n: 3, floor: false });
    expect(bins[1]).toMatchObject({ flux: 6, lo: 5, hi: 7, n: 2 });
  });

  it("skips frames without a positive flux and marks saturated visits as floors", () => {
    const bins = binByVisit([pt(10, null), pt(10.1, -1), pt(10.2, 4, 1, true), pt(10.3, 5, 1, true), pt(10.4, 6)]);
    expect(bins.length).toBe(1);
    expect(bins[0].n).toBe(3);
    expect(bins[0].floor).toBe(true);
  });

  it("sorts by time even when the input is not", () => {
    expect(binByVisit([pt(50, 1), pt(5, 2)]).map((b) => b.t)).toEqual([5, 50]);
  });
});

describe("photometry helpers", () => {
  it("picks the detector with the most measured frames", () => {
    expect(busiestDetector([pt(1, 1, 3), pt(2, 1, 3), pt(3, 1, 5), pt(4, null, 5), pt(5, null, 5)])).toBe(3);
    expect(busiestDetector([])).toBeNull();
  });

  it("orders spectrum points by wavelength and drops empty ones", () => {
    const s = spectrumPoints([
      { lambda_um: 2, flux_jy: 1, scatter_jy: null, n: 1, lower_limit: false },
      { lambda_um: 1, flux_jy: 2, scatter_jy: null, n: 1, lower_limit: false },
      { lambda_um: 3, flux_jy: null, scatter_jy: null, n: 1, lower_limit: false },
    ]);
    expect(s.map((p) => p.lambda_um)).toEqual([1, 2]);
  });

  it("marks features inside SPHEREx's range, in order", () => {
    const um = FEATURES.map((f) => f.um);
    expect(um.every((x) => x > 0.75 && x < 5)).toBe(true);
    expect(um).toEqual([...um].sort((a, b) => a - b));
  });
});
