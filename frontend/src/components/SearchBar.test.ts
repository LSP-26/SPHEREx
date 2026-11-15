import { describe, expect, it } from "vitest";
import { EXAMPLES } from "../lib/examples";
import type { Showcase } from "../lib/showcases";
import { currentText, planSearch, type Current } from "./SearchBar";

const barnard = EXAMPLES.find((e) => e.key === "barnard")!;
const wise = EXAMPLES.find((e) => e.key === "wise0855")!;
const show = (key: string, ra: number, dec: number, fov: number): Showcase => ({
  key, name: key, ra, dec, fov_arcmin: fov, n_frames: 1, elapsed_s: 1, events: `${key}/events.json`, thumb: "t.png", note: "",
});
const showcases = [show("barnard", 269.446223, 4.768671, 5), show("wise0855", 133.759961, -7.242259, 10)];

describe("planSearch", () => {
  it("re-runs the field on screen at a new field of view without a lookup", () => {
    const cur: Current = { ra: 10.5, dec: -3.25, name: "My star", fov: 5 };
    expect(planSearch(currentText(cur), 20, cur)).toEqual({ kind: "live", ra: 10.5, dec: -3.25, name: "My star", fov: 20 });
  });

  it("works for an unnamed field, whose box shows its position", () => {
    const cur: Current = { ra: 133.79513, dec: -7.24515, name: null, fov: 5 };
    expect(currentText(cur)).toBe("133.79513 -7.24515");
    expect(planSearch(" 133.79513 -7.24515 ", 30, cur)).toMatchObject({ kind: "live", ra: 133.79513, fov: 30 });
  });

  it("keeps a recorded example when its field of view is unchanged, and goes live when it changes", () => {
    const cur: Current = { ra: 269.446223, dec: 4.768671, name: "Barnard's Star", fov: 5, example: barnard };
    expect(planSearch("Barnard's Star", 5, cur)).toEqual({ kind: "example", example: barnard });
    expect(planSearch("Barnard's Star", 15, cur)).toEqual({ kind: "live", ra: 269.446223, dec: 4.768671, name: "Barnard's Star", fov: 15 });
  });

  it("uses an example's stored 2026 position at a new field of view, not a SIMBAD lookup", () => {
    expect(planSearch("WISE 0855", 5, undefined, showcases)).toEqual({ kind: "example", example: wise });
    expect(planSearch("WISE 0855", 10, undefined, showcases)).toEqual({ kind: "example", example: wise });
    expect(planSearch("wise 0855", 30, undefined, showcases)).toEqual({ kind: "live", ra: 133.759961, dec: -7.242259, name: "WISE 0855", fov: 30 });
  });

  it("leaves anything else to the name or coordinate lookup", () => {
    expect(planSearch("Vega", 5, undefined, showcases)).toBeNull();
    const cur: Current = { ra: 1, dec: 2, name: "A", fov: 5 };
    expect(planSearch("B", 5, cur, showcases)).toBeNull();
  });
});
