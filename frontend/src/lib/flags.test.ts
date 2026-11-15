import { describe, expect, it } from "vitest";
import { fetchFlags, hasFlags } from "./flags";

const meta = { frame_id: "f", width: 3, height: 3, categories: [], bits: {} };
const target = { ra: 1, dec: 2, fov_arcmin: 5, orient: "north" as const };
const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));

describe("flag layers", () => {
  it("only single catalogue frames have flags", () => {
    expect(hasFlags({ stage: "preview", label: { detector: 2 } })).toBe(true);
    expect(hasFlags({ stage: "refined", label: { detector: 2 } })).toBe(false);
    expect(hasFlags({ stage: "preview", label: { detector: 0 } })).toBe(false);
  });

  it("asks the server for a live field and prefixes the picture address", async () => {
    const urls: string[] = [];
    const r = await fetchFlags(target, "f", "http://api", (u) => { urls.push(String(u)); return json({ ...meta, png_url: "/flags/x.png" }); });
    expect(urls[0]).toContain("http://api/flags?");
    expect(urls[0]).toContain("frame=f");
    expect(r.ok && r.data.png_url).toBe("http://api/flags/x.png");
  });

  it("reads the recorded file of an example and points at its pictures", async () => {
    const urls: string[] = [];
    const file = { version: 1, frames: { g: { ...meta, frame_id: "g", png_url: "flags/00.png" } } };
    const r = await fetchFlags({ ...target, staticUrl: "/showcases/zz/flags.json" }, "g", "", (u) => { urls.push(String(u)); return json(file); });
    expect(urls).toEqual(["/showcases/zz/flags.json"]);
    expect(r.ok && r.data.png_url).toBe("/showcases/zz/flags/00.png");
    const missing = await fetchFlags({ ...target, staticUrl: "/showcases/zz/flags.json" }, "nope", "", () => json(file));
    expect(missing.ok).toBe(false);
  });

  it("says so when the server cannot be reached", async () => {
    const r = await fetchFlags(target, "f", "", () => Promise.reject(new Error("down")));
    expect(r.ok).toBe(false);
  });
});
