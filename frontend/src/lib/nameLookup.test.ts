import { describe, expect, it, vi } from "vitest";
import { adqlString, resolveName } from "./nameLookup";

const reply = (body: unknown, ok = true) => vi.fn(async (_url: string | URL | Request, _init?: RequestInit) => ({ ok, json: async () => body }) as Response);

describe("resolveName", () => {
  it("escapes quotes in the name", () => {
    expect(adqlString("Barnard's Star")).toBe("'Barnard''s Star'");
  });

  it("returns the position of the first match", async () => {
    const f = reply({ data: [["* Barnard's star", 269.4521, 4.6934]] });
    const r = await resolveName("Barnard's Star", f);
    expect(r).toEqual({ ok: true, name: "* Barnard's star", ra: 269.4521, dec: 4.6934, pmra: null, pmdec: null });
    const url = String(f.mock.calls[0][0]);
    expect(url.startsWith("https://simbad.cfa.harvard.edu/")).toBe(true);
    expect(decodeURIComponent(url.replace(/\+/g, " "))).toContain("id = 'Barnard''s Star'");
  });

  it("says so when nothing matches", async () => {
    const r = await resolveName("zzz", reply({ data: [] }));
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("zzz");
  });

  it("falls back to typing coordinates when the service is down", async () => {
    const down = vi.fn(async () => { throw new Error("offline"); });
    const r = await resolveName("Vega", down as unknown as typeof fetch);
    expect(r.ok).toBe(false);
    const bad = await resolveName("Vega", reply({}, false));
    expect(bad.ok).toBe(false);
  });

  it("does not call the service for an empty name", async () => {
    const f = reply({});
    expect((await resolveName("  ", f)).ok).toBe(false);
    expect(f).not.toHaveBeenCalled();
  });
});
