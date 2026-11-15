import { describe, expect, it } from "vitest";
import { parseDec, parseRa } from "./coords";

const ok = (v: number) => expect.objectContaining({ ok: true, value: expect.closeTo(v, 5) });

describe("parseRa", () => {
  it("reads decimal degrees", () => {
    expect(parseRa("133.7951")).toEqual(ok(133.7951));
    expect(parseRa("  0 ")).toEqual(ok(0));
    expect(parseRa("359.999999")).toEqual(ok(359.999999));
  });

  it("reads the WISE 0855 position from the NASA brief, in hours", () => {
    for (const text of ["08h55m10.831s", "8:55:10.831", "8 55 10.831", "08h 55m 10.831s", "8H55M10.831S"]) {
      expect(parseRa(text)).toEqual(ok(133.795129));
    }
  });

  it("reads hours with fewer parts, and degrees written with d", () => {
    expect(parseRa("12h")).toEqual(ok(180));
    expect(parseRa("12h30m")).toEqual(ok(187.5));
    expect(parseRa("133d47m42.5s")).toEqual(ok(133 + 47 / 60 + 42.5 / 3600));
  });

  it("rejects what it cannot read, with a reason", () => {
    for (const bad of ["", "abc", "1e3", "12 61 00", "12 30 61", "1 2 3 4", "-5", "360", "25h", "1,5"]) {
      const r = parseRa(bad);
      expect(r.ok, bad).toBe(false);
      if (!r.ok) expect(r.error.length).toBeGreaterThan(5);
    }
  });
});

describe("parseDec", () => {
  it("reads decimal degrees with or without a sign", () => {
    expect(parseDec("-7.2452")).toEqual(ok(-7.2452));
    expect(parseDec("+4.693")).toEqual(ok(4.693));
    expect(parseDec("90")).toEqual(ok(90));
    expect(parseDec("-90")).toEqual(ok(-90));
  });

  it("reads the WISE 0855 position from the NASA brief", () => {
    for (const text of ["-7d14m42.53s", "-07:14:42.53", "-7 14 42.53", "-7° 14′ 42.53″"]) {
      expect(parseDec(text)).toEqual(ok(-7.245147));
    }
  });

  it("keeps the sign when the degrees part is zero", () => {
    expect(parseDec("-0 30 00")).toEqual(ok(-0.5));
    expect(parseDec("+0d30m")).toEqual(ok(0.5));
  });

  it("rejects out-of-range and unreadable input", () => {
    for (const bad of ["", "x", "91", "-90.001", "10 70 00", "1 2 3 4"]) {
      expect(parseDec(bad).ok, bad).toBe(false);
    }
  });
});
