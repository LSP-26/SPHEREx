import { describe, expect, it } from "vitest";
import { findExample } from "./examples";
import { describeStar, distanceLy, fastMovers, motionArcsecYr, nearestStar } from "./gaia";
import { bandWavelength, channelsOf, colourPasses, defaultBand, looksByBand, monthYear, passes } from "./looks";
import type { OverlayData, OverlayStar } from "./overlay";
import { readPage, writePage } from "./page";
import { composite, difference, grayFromRgba, meanOf, skyStats, type Gray } from "./pixels";
import type { Slot } from "./reducer";
import { reportText, simbadUrl } from "./report";
import { parseCoords, propagate, splitCoords } from "./search";
import { formatDec, formatRa, pictureSizePx, pictureToSky, separationArcsec, skyToPicture } from "./sky";
import { withDefaults } from "./urlState";

function frame(slot: number, detector: number, t: number, lambda = 1): Slot {
  return {
    type: "frame", slot, stage: "preview", png_url: `f${slot}.png`, width: 5, height: 5, visit_index: 0, elapsed_s: 0,
    label: { frame_id: `id${slot}`, detector, lambda_um: lambda, channel: null, t_mid_mjd: t, release: "QR2", version: "v1", flags_summary: [] },
  } as Slot;
}

describe("looks", () => {
  // two passes, six months apart; D1 seen twice, D5 once, D3 twice
  const slots = [frame(0, 1, 61000, 0.75), frame(1, 5, 61001, 3.9), frame(2, 3, 61002, 1.65), null,
    frame(4, 1, 61180, 0.76), frame(5, 3, 61182, 1.66)];

  it("groups frames by detector, oldest first", () => {
    const by = looksByBand(slots);
    expect(by.get(1)!.map((l) => l.slot)).toEqual([0, 4]);
    expect(by.get(5)!.length).toBe(1);
    expect(by.has(2)).toBe(false);
  });

  it("opens on the band with most looks, preferring the sharp short bands", () => {
    expect(defaultBand(looksByBand(slots))).toBe(1);
    expect(defaultBand(looksByBand([frame(0, 4, 1), frame(1, 4, 2), frame(2, 6, 1), frame(3, 6, 2)]))).toBe(6);
    expect(defaultBand(new Map())).toBeNull();
  });

  it("labels a band with its median wavelength", () => {
    expect(bandWavelength(looksByBand(slots).get(3)!)).toBeCloseTo(1.66);
  });

  it("splits frames into passes over the sky by gaps in time", () => {
    const ps = passes(slots);
    expect(ps.length).toBe(2);
    expect([...ps[0].byDetector.keys()].sort()).toEqual([1, 3, 5]);
    expect(channelsOf(ps[0])).toEqual(["r", "g", "b"]);
    expect(channelsOf(ps[1])).toEqual(["g", "b"]);
    expect(colourPasses(slots).length).toBe(2);
  });

  it("formats dates", () => {
    expect(monthYear(61000)).toBe("Nov 2025");
  });
});

function gray(values: number[], ok?: number[]): Gray {
  const n = values.length;
  return { width: n, height: 1, v: Float32Array.from(values), ok: Uint8Array.from(ok ?? values.map(() => 1)) };
}

describe("pixels", () => {
  it("reads grey and 'no data' from RGBA", () => {
    const g = grayFromRgba(Uint8Array.from([10, 10, 10, 255, 200, 200, 200, 0]), 2, 1);
    expect([...g.v]).toEqual([10, 200]);
    expect([...g.ok]).toEqual([1, 0]);
  });

  it("measures the sky level and noise robustly", () => {
    const s = skyStats(gray([10, 10, 12, 8, 10, 255]));
    expect(s.level).toBe(10);
    expect(s.sigma).toBeGreaterThanOrEqual(1);
  });

  it("cancels what did not change and keeps what did", () => {
    const sky = [20, 22, 18, 20, 21, 19, 20, 20];
    const before = gray([...sky.slice(0, 3), 200, ...sky.slice(4)]);
    const after = gray([...sky.slice(0, 5), 200, ...sky.slice(6)]);
    const d = difference(after, before);
    const at = (i: number) => d[i * 4];
    expect(at(5)).toBe(255); // brighter now: white
    expect(at(3)).toBe(0); // brighter before: black
    expect(Math.abs(at(0) - 128)).toBeLessThan(40); // unchanged sky stays near grey
  });

  it("leaves no-data pixels transparent in a difference", () => {
    const d = difference(gray([1, 2, 3], [1, 0, 1]), gray([1, 2, 3]));
    expect(d[1 * 4 + 3]).toBe(0);
    expect(d[0 * 4 + 3]).toBe(255);
  });

  it("averages frames and builds a colour picture with a missing channel black", () => {
    const m = meanOf([gray([10, 20]), gray([30, 40], [1, 0])])!;
    expect([...m.v]).toEqual([20, 20]);
    const c = composite({ r: gray([100, 0]), g: null, b: gray([50, 60]) });
    expect([...c.slice(0, 4)]).toEqual([100, 0, 50, 255]);
  });
});

describe("sky", () => {
  const f = { ra: 269.45, dec: 4.77, size: 49 };
  it("puts the field centre in the middle, north up and east left", () => {
    const c = pictureToSky(0.5, 0.5, f);
    expect(c.ra).toBeCloseTo(269.45, 9);
    expect(c.dec).toBeCloseTo(4.77, 9);
    const n = pictureToSky(0.5, 0.25, f);
    expect(n.dec).toBeGreaterThan(f.dec);
    const e = pictureToSky(0.25, 0.5, f);
    expect(e.ra).toBeGreaterThan(f.ra);
  });

  it("goes back and forth", () => {
    const s = pictureToSky(0.3, 0.7, f);
    const p = skyToPicture(s.ra, s.dec, f);
    expect(p.u).toBeCloseTo(0.3, 6);
    expect(p.v).toBeCloseTo(0.7, 6);
  });

  it("uses the server's picture size", () => {
    expect(pictureSizePx(5)).toBe(49);
    expect(pictureSizePx(10)).toBe(99);
  });

  it("formats coordinates", () => {
    expect(formatRa(133.795129)).toBe("8h 55m 10.8s");
    expect(formatDec(-7.245147)).toBe("−07° 14′ 43″");
    expect(separationArcsec(10, 0, 10, 1 / 3600)).toBeCloseTo(1, 6);
  });
});

describe("search", () => {
  it("recognises coordinates in the usual ways", () => {
    expect(splitCoords("269.45 4.69")).toEqual(["269.45", "4.69"]);
    expect(splitCoords("269.45, -4.69")).toEqual(["269.45", "-4.69"]);
    expect(splitCoords("08h55m10.831s -07d14m42.53s")).toEqual(["08h55m10.831s", "-07d14m42.53s"]);
    expect(splitCoords("8 55 10.8 -7 14 42")).toEqual(["8 55 10.8", "-7 14 42"]);
    expect(splitCoords("Barnard's Star")).toBeNull();
    expect(splitCoords("R Dra")).toBeNull();
  });

  it("parses them to degrees", () => {
    const r = parseCoords("08h55m10.831s -07d14m42.53s");
    expect(r?.ok).toBe(true);
    if (r?.ok) {
      expect(r.ra).toBeCloseTo(133.795129, 5);
      expect(r.dec).toBeCloseTo(-7.245147, 5);
    }
    expect(parseCoords("400 10")?.ok).toBe(false);
  });

  it("moves a fast star to today: Barnard's Star goes about 270 arcsec north", () => {
    const p = propagate(269.452, 4.6934, -801.6, 10362.4, 2026);
    expect((p.dec - 4.6934) * 3600).toBeCloseTo(269.4, 0);
    expect(p.ra).toBeLessThan(269.452);
  });

  it("knows the challenge targets by their common names", () => {
    expect(findExample("barnard's star")?.key).toBe("barnard");
    expect(findExample("WISE 0855")?.key).toBe("wise0855");
    expect(findExample("eps indi b")?.key).toBe("eps_indi_b");
    expect(findExample("R Dra")?.key).toBe("r_dra");
    expect(findExample("Vega")).toBeUndefined();
  });
});

describe("page URL", () => {
  it("reads and writes an example view", () => {
    const pg = readPage("?ex=barnard&band=2&mode=diff");
    expect(pg.source).toEqual({ kind: "example", key: "barnard" });
    expect(pg.band).toBe(2);
    expect(pg.mode).toBe("diff");
    expect(writePage(pg)).toBe("ex=barnard&band=2&mode=diff");
  });

  it("reads a live field without mistaking view keys for API parameters", () => {
    const pg = readPage("?ra=133.79&dec=-7.24&fov=10&name=WISE%200855&band=colour&embed=1");
    expect(pg.errors).toEqual([]);
    expect(pg.source).toEqual({ kind: "live", params: withDefaults({ ra: 133.79, dec: -7.24, fov_arcmin: 10 }) });
    expect(pg.band).toBe("colour");
    expect(pg.embed).toBe(true);
    expect(pg.name).toBe("WISE 0855");
  });

  it("keeps the api server on shared links", () => {
    const pg = readPage("?ex=r_dra");
    expect(writePage(pg, "?ex=x&api=https%3A%2F%2Fab.lhr.life")).toBe("ex=r_dra&api=https%3A%2F%2Fab.lhr.life");
  });

  it("reports a broken link", () => {
    expect(readPage("?ra=500&dec=0").errors.length).toBeGreaterThan(0);
    expect(readPage("?ex=barnard&band=9").errors).toEqual(["band must be 1 to 6 or colour"]);
    expect(readPage("").source).toBeNull();
  });
});

describe("gaia", () => {
  const star = (o: Partial<OverlayStar>): OverlayStar => ({
    source_id: 1, x: 24, y: 24, u: 0.5, v: 0.5, vx: 0, vy: 0, g_mag: 10, pmra: 0, pmdec: 0, parallax: 1, ...o,
  });
  const barnard = star({ source_id: 7, pmra: -801.55, pmdec: 10362.4, parallax: 546.98, g_mag: 8.19, vy: -1.685 });
  const data: OverlayData = { size: 49, epoch_mjd: 61100, pixel_arcsec: 6.15, stars: [star({ x: 5, y: 5, pmra: 2, pmdec: 1 }), barnard] };

  it("describes motion and distance", () => {
    expect(motionArcsecYr(barnard)).toBeCloseTo(10.39, 2);
    expect(distanceLy(barnard)).toBeCloseTo(5.96, 2);
    expect(describeStar(barnard)).toBe("moves 10.4″ a year · 6.0 light-years away · G 8.2");
    expect(distanceLy(star({ parallax: 0.2 }))).toBeNull();
  });

  it("lists the fast movers, fastest first", () => {
    expect(fastMovers(data).map((s) => s.source_id)).toEqual([7]);
  });

  it("finds the star under a click, at that date", () => {
    expect(nearestStar(data, 24.5 / 49, 24.5 / 49, 61100)?.star.source_id).toBe(7);
    // two years later Barnard's Star has moved 3.4 px up, so the old spot is empty
    expect(nearestStar(data, 24.5 / 49, 24.5 / 49, 61100 + 2 * 365.25, 1)).toBeNull();
  });
});

describe("report", () => {
  it("carries everything needed to check a find", () => {
    const t = reportText({ ra: 133.795, dec: -7.245, link: "https://x/?ex=wise0855", band: "4.6 µm", looks: ["Nov 2025", "May 2026"], known: null });
    expect(t).toContain("RA 133.79500°, Dec -7.24500°");
    expect(t).toContain("https://x/?ex=wise0855");
    expect(t).toContain("Nov 2025, May 2026");
    expect(simbadUrl(1, 2)).toContain("Coord=1.00000+%2B2.00000");
  });
});
