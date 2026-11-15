import { describe, expect, it } from "vitest";
import { eventsSrc, liveHref, overlaySrc, parseManifest, showcaseFor, thumbSrc, type Showcase } from "./showcases";

const S: Showcase = {
  key: "barnard", name: "Barnard's Star", ra: 269.4462, dec: 4.7687, fov_arcmin: 10, n_frames: 18,
  elapsed_s: 3.1, events: "barnard/events.json", thumb: "frames/00_preview.png", note: "",
};

describe("showcases", () => {
  it("reads a manifest and skips entries that are the wrong shape", () => {
    expect(parseManifest({ showcases: [S, { key: "x" }, null, "y"] })).toEqual([S]);
  });

  it("gives an empty list for anything that is not a manifest", () => {
    for (const bad of [null, {}, [], "x", { showcases: "no" }]) expect(parseManifest(bad)).toEqual([]);
  });

  it("builds links the page understands", () => {
    expect(eventsSrc(S)).toBe("/showcases/barnard/events.json");
    expect(liveHref(S)).toBe("?ra=269.4462&dec=4.7687&fov=10");
    expect(thumbSrc(S)).toBe("/showcases/barnard/frames/00_preview.png");
  });
});

describe("showcaseFor", () => {
  const s = { key: "barnard", name: "B", ra: 1, dec: 2, fov_arcmin: 5, n_frames: 3, elapsed_s: 1,
    events: "barnard/events.json", thumb: "frames/00_preview.png", note: "" };
  it("finds the showcase a replay URL plays and where its stars are saved", () => {
    expect(showcaseFor("/showcases/barnard/events.json", [s])).toBe(s);
    expect(overlaySrc(s)).toBe("/showcases/barnard/overlay.json");
    expect(showcaseFor("/somewhere/else.json", [s])).toBeUndefined();
    expect(showcaseFor(null, [s])).toBeUndefined();
  });
});
