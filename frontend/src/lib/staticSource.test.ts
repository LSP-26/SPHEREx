import { describe, expect, it, vi } from "vitest";
import type { FlipbookEvent } from "./events";
import { replayStatic } from "./staticSource";

const label = { frame_id: "f", detector: 1, lambda_um: 1, t_mid_mjd: 61000, release: "QR3", version: "v27" };
const events = [
  { type: "heartbeat" },
  { type: "plan", job_id: "j", n_slots: 1, n_visits_total: 1, n_visits_shown: 1 },
  { type: "frame", slot: 0, stage: "preview", png_url: "frames/a.png", width: 49, height: 49, label, visit_index: 0, elapsed_s: 1 },
  { type: "mystery" },
  { type: "done", n_frames: 1, elapsed_s: 1 },
  { type: "frame", slot: 1, stage: "preview", png_url: "never.png", width: 1, height: 1, label, visit_index: 0, elapsed_s: 1 },
];
const file = (body: unknown, status = 200) => vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;
const run = async (fetchFn: typeof fetch, url = "https://cdn.example.org/showcases/barnard/events.json") => {
  const got: FlipbookEvent[] = [];
  replayStatic(url, { onEvent: (e) => void got.push(e) }, { fetchFn });
  await new Promise((r) => setTimeout(r, 20));
  return got;
};

describe("replayStatic", () => {
  it("replays recorded events in order, resolves frame URLs next to the file, stops at done", async () => {
    const got = await run(file({ events }));
    expect(got.map((e) => e.type)).toEqual(["heartbeat", "plan", "frame", "done"]);
    const f = got[2];
    expect(f.type === "frame" && f.png_url).toBe("https://cdn.example.org/showcases/barnard/frames/a.png");
  });

  it("also accepts a bare array of events", async () => {
    expect((await run(file(events))).length).toBe(4);
  });

  it("a missing or broken file is a clear error event", async () => {
    for (const fetchFn of [file({}, 404), vi.fn(async () => { throw new TypeError("offline"); }) as unknown as typeof fetch]) {
      const got = await run(fetchFn);
      expect(got).toHaveLength(1);
      expect(got[0]).toMatchObject({ type: "error", code: "internal", retryable: false });
    }
  });

  it("cancel stops the replay", async () => {
    const got: FlipbookEvent[] = [];
    const r = replayStatic("https://x.org/e.json", { onEvent: (e) => void got.push(e) }, { fetchFn: file(events), delayMs: 30 });
    await new Promise((res) => setTimeout(res, 40));
    r.cancel();
    const n = got.length;
    await new Promise((res) => setTimeout(res, 150));
    expect(got.length).toBe(n);
    expect(n).toBeLessThan(4);
  });
});
