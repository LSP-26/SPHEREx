import { describe, expect, it } from "vitest";
import { resolveApiBase } from "./apiBase";

describe("resolveApiBase", () => {
  it("accepts a trycloudflare tunnel and drops a trailing slash", () => {
    expect(resolveApiBase("?api=https://abc-def.trycloudflare.com/")).toBe("https://abc-def.trycloudflare.com");
  });
  it("accepts a localhost.run tunnel", () => {
    expect(resolveApiBase("?api=https://56711a13b5afe2.app.lhr.life")).toBe("https://56711a13b5afe2.app.lhr.life");
  });
  it("accepts localhost", () => {
    expect(resolveApiBase("?api=http://localhost:8000")).toBe("http://localhost:8000");
  });
  it("rejects anything else and uses the fallback", () => {
    for (const bad of ["https://evil.example.com", "https://trycloudflare.com.evil.com", "http://abc.trycloudflare.com", "https://lhr.life.evil.com"])
      expect(resolveApiBase(`?api=${bad}`, "X")).toBe("X");
    expect(resolveApiBase("", "X")).toBe("X");
  });
});
