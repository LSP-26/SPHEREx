// End to end: this TypeScript client against a REAL API server over real HTTP.
//   python -m backend.server --fake-latency 0.15 --cache ""      (in one terminal)
//   E2E_API=http://127.0.0.1:8000 npm run e2e                      (in another)
// Uses the `eventsource` package, a spec-compliant EventSource for Node (browsers have their own).
import { EventSource } from "eventsource";
import { describe, expect, it } from "vitest";
import type { FlipbookEvent } from "../lib/events";
import { startFlipbook, type EventSourceLike } from "../lib/stream";

const API = process.env.E2E_API ?? "http://127.0.0.1:8000";
const POS = "ra=10&dec=0"; // the synthetic fixture the API serves in fixtures mode

type Ctor = new (url: string) => EventSourceLike;
const RealES = EventSource as unknown as Ctor;

/** An EventSource that connects (so the server sees a watcher) but delivers nothing to the page,
 *  like a proxy that holds the whole response back. */
class BufferedES implements EventSourceLike {
  onmessage: EventSourceLike["onmessage"] = null;
  onerror: EventSourceLike["onerror"] = null;
  readyState = 1;
  private inner: EventSourceLike;
  constructor(url: string) {
    this.inner = new RealES(url);
  }
  close() {
    this.readyState = 2;
    this.inner.close();
  }
}

/** Forwards the first `n` events, then goes silent (a connection that dies half way). */
function dyingES(n: number): Ctor {
  return class extends BufferedES {
    constructor(url: string) {
      super(url);
      let seen = 0;
      const inner = (this as unknown as { inner: EventSourceLike }).inner;
      inner.onmessage = (m) => {
        if (seen++ < n) this.onmessage?.(m);
      };
    }
  };
}

function run(query: string, deps: Parameters<typeof startFlipbook>[2] = {}) {
  return new Promise<{ events: FlipbookEvent[]; transports: string[] }>((resolve) => {
    const events: FlipbookEvent[] = [];
    const transports: string[] = [];
    startFlipbook(
      query,
      {
        onEvent: (e) => {
          events.push(e);
        },
        onTransport: (t) => {
          transports.push(t);
          if (t === "closed") resolve({ events, transports });
        },
      },
      { apiBase: API, EventSourceCtor: RealES, silenceFirstMs: 400, silenceLaterMs: 400, pollWaitS: 3, ...deps },
    );
  });
}

const kinds = (events: FlipbookEvent[]) => events.map((e) => e.type);
const frames = (events: FlipbookEvent[]) => events.filter((e) => e.type === "frame");

describe("against the real server", () => {
  it("streams a full flipbook with a real EventSource", async () => {
    const { events, transports } = await run(POS);
    expect(kinds(events).slice(0, 3)).toEqual(["heartbeat", "plan", "progress"]);
    expect(events.at(-1)?.type).toBe("done");
    expect(frames(events)).toHaveLength(4);
    expect(transports).toContain("stream");
    expect(transports).not.toContain("poll");
    const f = frames(events)[0];
    expect(f.type === "frame" && f.png_url.startsWith(API + "/frames/")).toBe(true);
    const png = await fetch(f.type === "frame" ? f.png_url : "");
    expect(new Uint8Array(await png.arrayBuffer()).slice(0, 4)).toEqual(new Uint8Array([0x89, 0x50, 0x4e, 0x47]));
  });

  it("polling alone gives the same flipbook (no EventSource available)", async () => {
    const viaStream = await run(POS);
    const viaPoll = await run(POS, { EventSourceCtor: undefined });
    expect(viaPoll.transports).toContain("poll");
    expect(kinds(viaPoll.events).filter((k) => k !== "heartbeat")).toEqual(kinds(viaStream.events).filter((k) => k !== "heartbeat"));
    const labels = (r: typeof viaPoll) => frames(r.events).map((e) => (e.type === "frame" ? e.label.frame_id : ""));
    expect(labels(viaPoll).sort()).toEqual(labels(viaStream).sort());
  });

  it("falls back to polling when a proxy buffers the stream, and still finishes", async () => {
    const t0 = Date.now();
    const { events, transports } = await run(`${POS}&fov=6`, { EventSourceCtor: BufferedES });
    expect(transports).toContain("poll");
    expect(transports).not.toContain("stream");
    expect(events.at(-1)?.type).toBe("done");
    expect(frames(events)).toHaveLength(4);
    expect(Date.now() - t0).toBeGreaterThanOrEqual(400); // it waited for the silence timeout
  });

  it("resumes by polling after a stream that dies half way, with no event lost or repeated", async () => {
    const whole = await run(`${POS}&fov=7`);
    const resumed = await run(`${POS}&fov=7`, { EventSourceCtor: dyingES(4) });
    expect(resumed.transports).toContain("poll");
    // the job is shared and finished, so the polled tail is exactly the rest of its list
    const strip = (r: typeof whole) => kinds(r.events).filter((k) => k !== "heartbeat");
    expect(strip(resumed)).toEqual(strip(whole));
    const slots = frames(resumed.events).map((e) => (e.type === "frame" ? e.slot : -1));
    expect(new Set(slots).size).toBe(slots.length);
  });

  it("a bad request is a clear error from the real 422", async () => {
    const { events } = await run("ra=400&dec=0");
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: "error", code: "bad_coords", retryable: false });
  });

  it("a position with no data is a no_coverage error event", async () => {
    const { events } = await run("ra=200&dec=-60");
    expect(events.at(-1)).toMatchObject({ type: "error", code: "no_coverage" });
  });

  it("two browsers asking for the same thing share one job", async () => {
    const [a, b] = await Promise.all([run(`${POS}&fov=9`), run(`${POS}&fov=9`)]);
    const planOf = (r: typeof a) => r.events.find((e) => e.type === "plan");
    expect(planOf(a)?.type === "plan" && planOf(a)).toBeTruthy();
    expect((planOf(a) as { job_id: string }).job_id).toBe((planOf(b) as { job_id: string }).job_id);
  });
});
