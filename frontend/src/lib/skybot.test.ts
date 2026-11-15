import { describe, expect, it } from "vitest";
import { defaultLimit, epochKey, fetchAsteroids, namesByBrightness, parseSexagesimal, parseSkybot, positionAt, projectToPicture, trackFor } from "./skybot";

describe("skybot", () => {
  it("parses sexagesimal and plain numbers", () => {
    expect(parseSexagesimal("08 30 00")).toBeCloseTo(8.5);
    expect(parseSexagesimal("-10:30:00")).toBeCloseTo(-10.5);
    expect(parseSexagesimal(12.5)).toBe(12.5);
    expect(parseSexagesimal("")).toBeNull();
  });

  it("reads rows, converting hours to degrees", () => {
    const rows = parseSkybot({ data: [{ Name: "Ceres", "RA (hour)": 6, "DEC (deg)": -5, "VMag (mag)": 8.9 }] });
    expect(rows).toEqual([{ name: "Ceres", ra: 90, dec: -5, vmag: 8.9 }]);
    expect(parseSkybot("junk")).toEqual([]);
  });

  it("projects with east to the left and north up", () => {
    const c = projectToPicture({ ra: 10, dec: 0 }, 10, 0, 5);
    expect(c.u).toBeCloseTo(0.5);
    expect(projectToPicture({ ra: 10.001, dec: 0 }, 10, 0, 5).u).toBeLessThan(0.5);
    expect(projectToPicture({ ra: 10, dec: 0.001 }, 10, 0, 5).v).toBeLessThan(0.5);
  });

  it("groups frames by date and fails only if all requests fail", async () => {
    expect(epochKey(60000.01)).toBe(epochKey(60000.02));
    const ok = async () => new Response(JSON.stringify([{ Name: "A", RA: 10, DEC: 0, VMag: 18 }]));
    const r = await fetchAsteroids({ ra: 10, dec: 0, fov_arcmin: 5 }, [60000.01, 60000.02, 60001], ok as typeof fetch);
    expect(r.ok && r.byEpoch.size).toBe(2);
    const bad = async () => { throw new Error("blocked"); };
    expect((await fetchAsteroids({ ra: 1, dec: 1, fov_arcmin: 5 }, [60000], bad as typeof fetch)).ok).toBe(false);
  });

  it("treats an empty or plain-text answer as no asteroids", async () => {
    const empty = async () => new Response("No solar system object found");
    const r = await fetchAsteroids({ ra: 10, dec: 0, fov_arcmin: 5 }, [60000], empty as typeof fetch);
    expect(r.ok && r.missing).toBe(0);
  });

  it("shows everything when few, and a cut when many", () => {
    expect(defaultLimit([{ name: "A", ra: 0, dec: 0, vmag: 20 }])).toBe(30);
    const many = Array.from({ length: 40 }, (_, i) => ({ name: `n${i}`, ra: 0, dec: 0, vmag: 15 + i / 4 }));
    expect(defaultLimit(many)).toBeLessThan(25);
  });

  it("follows one object: straight line between dates, across RA 0, brightest names first", () => {
    const by = new Map([
      [1, [{ name: "A", ra: 359.99, dec: 0, vmag: 18 }, { name: "B", ra: 5, dec: 5, vmag: 15 }]],
      [2, [{ name: "A", ra: 0.01, dec: 0.02, vmag: 18.5 }]],
    ]);
    const tr = trackFor("A", by);
    expect(tr).toHaveLength(2);
    const mid = positionAt(tr, 1.5)!;
    expect(mid.dec).toBeCloseTo(0.01);
    expect(Math.min(mid.ra, 360 - mid.ra)).toBeLessThan(0.001);
    expect(positionAt(tr, 0)).toEqual(tr[0]);
    expect(positionAt(tr, 9)).toEqual(tr[1]);
    expect(positionAt([], 1)).toBeNull();
    expect(namesByBrightness(by).map((n) => n.name)).toEqual(["B", "A"]);
  });
});
