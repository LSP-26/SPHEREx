import { describe, expect, it } from "vitest";
import { framesDiffer, frameWavelength, megabytes, micrometres, mjdToUtc, wavelengthsDiffer } from "./format";

describe("mjdToUtc", () => {
  it("matches well-known Modified Julian Dates", () => {
    expect(mjdToUtc(40587)).toBe("1970-01-01 00:00 UTC"); // the Unix epoch
    expect(mjdToUtc(51544)).toBe("2000-01-01 00:00 UTC"); // J2000 minus half a day
    expect(mjdToUtc(51544.5)).toBe("2000-01-01 12:00 UTC");
    expect(mjdToUtc(60676)).toBe("2025-01-01 00:00 UTC");
    expect(mjdToUtc(61041)).toBe("2026-01-01 00:00 UTC");
  });
});

describe("labels", () => {
  it("shows wavelengths with a sensible number of digits", () => {
    expect(micrometres(0.93)).toBe("0.930 µm");
    expect(micrometres(2.3712)).toBe("2.37 µm");
  });

  it("flags wavelength jumps above 5 percent only", () => {
    expect(wavelengthsDiffer(1.0, 1.04)).toBe(false);
    expect(wavelengthsDiffer(1.0, 1.06)).toBe(true);
    expect(wavelengthsDiffer(0.93, 1.37)).toBe(true);
  });

  it("formats sizes", () => {
    expect(megabytes(6_397_440)).toBe("6.4 MB");
    expect(megabytes(54_000_000)).toBe("54 MB");
  });
});

describe("merged colour frames", () => {
  it("have no single wavelength and never trigger the wavelength warning", () => {
    expect(frameWavelength({ detector: 0, lambda_um: 2.4 })).toBe("all detectors (colour)");
    expect(frameWavelength({ detector: 3, lambda_um: 2.4 })).toBe("2.40 µm");
    expect(framesDiffer({ detector: 0, lambda_um: 2.4 }, { detector: 1, lambda_um: 0.8 })).toBe(false);
    expect(framesDiffer({ detector: 2, lambda_um: 1.2 }, { detector: 1, lambda_um: 0.8 })).toBe(true);
  });
});
