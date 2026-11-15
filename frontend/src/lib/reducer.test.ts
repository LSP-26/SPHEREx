import { describe, expect, it } from "vitest";
import type { FlipbookEvent } from "./events";
import { filledSlots, initialState, reducer, type FlipbookState } from "./reducer";

const label = { frame_id: "f", detector: 1, lambda_um: 1, t_mid_mjd: 61000, release: "QR3", version: "v27" };
const plan = (n: number): FlipbookEvent => ({ type: "plan", job_id: "j", n_slots: n, n_visits_total: 9, n_visits_shown: 3 });
const frame = (slot: number, stage: "preview" | "refined" = "preview", url = `/frames/${slot}.png`): FlipbookEvent => ({
  type: "frame", slot, stage, png_url: url, width: 49, height: 49, label, visit_index: 0, elapsed_s: 1,
});
const run = (events: FlipbookEvent[], from: FlipbookState = reducer(initialState, { type: "start" })) =>
  events.reduce((s, event) => reducer(s, { type: "event", event }), from);

describe("reducer", () => {
  it("starts connecting and becomes running on the first heartbeat", () => {
    const s = reducer(initialState, { type: "start" });
    expect(s.phase).toBe("connecting");
    expect(run([{ type: "heartbeat" }]).phase).toBe("running");
  });

  it("a plan creates one empty slot per frame", () => {
    const s = run([{ type: "heartbeat" }, plan(4)]);
    expect(s.slots).toEqual([null, null, null, null]);
    expect(s.plan?.n_visits_total).toBe(9);
  });

  it("frames fill their own slot whatever order they arrive in", () => {
    const s = run([plan(4), frame(2), frame(0)]);
    expect(s.slots.map((x) => x?.slot ?? null)).toEqual([0, null, 2, null]);
    expect(filledSlots(s)).toEqual([0, 2]);
  });

  it("a refined frame replaces the preview in the same slot", () => {
    const s = run([plan(2), frame(1, "preview", "/p.png"), frame(1, "refined", "/r.png")]);
    expect(s.slots[1]?.png_url).toBe("/r.png");
    expect(s.slots[1]?.stage).toBe("refined");
  });

  it("copes with a frame that arrives before any plan", () => {
    expect(run([frame(2)]).slots.length).toBe(3);
  });

  it("tracks progress and finishes", () => {
    const s = run([plan(1), { type: "progress", done: 1, total: 1, stage: "preview" }, frame(0), { type: "done", n_frames: 1, elapsed_s: 2.5 }]);
    expect(s.phase).toBe("done");
    expect(s.progress).toEqual({ done: 1, total: 1, stage: "preview" });
    expect(s.done).toEqual({ n_frames: 1, elapsed_s: 2.5 });
  });

  it("an error keeps the frames that did arrive", () => {
    const s = run([plan(2), frame(0), { type: "error", code: "s3_down", message: "Store down.", retryable: true }]);
    expect(s.phase).toBe("error");
    expect(s.error).toEqual({ code: "s3_down", message: "Store down.", retryable: true });
    expect(filledSlots(s)).toEqual([0]);
  });

  it("start clears the previous run, and transport changes are recorded", () => {
    const old = run([plan(2), frame(0)]);
    const again = reducer(old, { type: "start" });
    expect(again.slots).toEqual([]);
    expect(reducer(again, { type: "transport", transport: "poll" }).transport).toBe("poll");
  });
});
