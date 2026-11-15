import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FlipbookEvent } from "./events";
import { startFlipbook, type EventSourceLike } from "./stream";

const HANDLE = { job_id: "J", stream_url: "/flipbook/J/stream", poll_url: "/flipbook/J/events", job_url: "/flipbook/J" };
const label = { frame_id: "f", detector: 1, lambda_um: 1, t_mid_mjd: 61000, release: "QR3", version: "v27" };
const frame = (slot: number, url = `/frames/${slot}.png`) => ({
  type: "frame", slot, stage: "preview", png_url: url, width: 49, height: 49, label, visit_index: 0, elapsed_s: 1,
});
const DONE = { type: "done", n_frames: 1, elapsed_s: 1 };

class FakeES implements EventSourceLike {
  static all: FakeES[] = [];
  onmessage: EventSourceLike["onmessage"] = null;
  onerror: EventSourceLike["onerror"] = null;
  readyState = 1;
  closed = false;
  constructor(public url: string) {
    FakeES.all.push(this);
  }
  send(event: object, id?: number) {
    this.onmessage?.({ data: JSON.stringify(event), lastEventId: id === undefined ? "" : String(id) });
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  fail() {
    this.readyState = 2;
    this.onerror?.({});
  }
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

/** A fake API. `poll` answers GET .../events requests in order. */
function fakeApi(opts: { start?: () => Response | Promise<Response>; poll?: (() => Response | Promise<Response>)[] } = {}) {
  const calls: { url: string; method: string }[] = [];
  const polls = [...(opts.poll ?? [])];
  const fetchFn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method });
    if (method === "POST") return opts.start ? opts.start() : json(HANDLE);
    if (url.includes("/events")) {
      const next = polls.shift();
      if (!next) throw new Error("no more fake poll answers");
      return next();
    }
    throw new Error(`unexpected ${method} ${url}`);
  }) as unknown as typeof fetch;
  return { fetchFn, calls };
}

function collect() {
  const events: FlipbookEvent[] = [];
  const transports: string[] = [];
  return { events, transports, handlers: { onEvent: (e: FlipbookEvent) => void events.push(e), onTransport: (t: string) => void transports.push(t) } };
}

const settle = () => vi.advanceTimersByTimeAsync(0);

beforeEach(() => {
  vi.useFakeTimers();
  FakeES.all = [];
});
afterEach(() => vi.useRealTimers());

const start = (api: ReturnType<typeof fakeApi>, c: ReturnType<typeof collect>, extra = {}) =>
  startFlipbook("ra=10&dec=0", c.handlers as never, { fetchFn: api.fetchFn, EventSourceCtor: FakeES, ...extra });

describe("streaming", () => {
  it("starts the job with POST, then streams its events and stops at done", async () => {
    const api = fakeApi();
    const c = collect();
    start(api, c);
    await settle();
    expect(api.calls).toEqual([{ url: "/flipbook?ra=10&dec=0", method: "POST" }]);
    const es = FakeES.all[0];
    expect(es.url).toBe("/flipbook/J/stream");
    es.send({ type: "heartbeat" }, 0);
    es.send(frame(0), 1);
    es.send(DONE, 2);
    expect(c.events.map((e) => e.type)).toEqual(["heartbeat", "frame", "done"]);
    expect(es.closed).toBe(true); // closing stops EventSource reconnecting
    expect(c.transports).toEqual(["connecting", "connecting", "stream", "closed"]);
  });

  it("ignores events it does not know, bad JSON and replays of events it already has", async () => {
    const c = collect();
    start(fakeApi(), c);
    await settle();
    const es = FakeES.all[0];
    es.send(frame(0), 0);
    es.send({ type: "from_the_future" }, 1);
    es.onmessage?.({ data: "not json", lastEventId: "" });
    es.send(frame(0), 0); // a reconnect replaying id 0
    es.send(frame(1), 2);
    expect(c.events.map((e) => (e.type === "frame" ? e.slot : e.type))).toEqual([0, 1]);
  });

  it("rewrites frame URLs for an API on another host", async () => {
    const c = collect();
    start(fakeApi(), c, { apiBase: "https://api.example.org" });
    await settle();
    FakeES.all[0].send(frame(0), 0);
    const f = c.events[0];
    expect(f.type === "frame" && f.png_url).toBe("https://api.example.org/frames/0.png");
    expect(FakeES.all[0].url).toBe("https://api.example.org/flipbook/J/stream");
  });

  it("idle heartbeats (no id) do not move the resume point", async () => {
    const api = fakeApi({ poll: [() => json({ events: [DONE], next: 5, done: true })] });
    const c = collect();
    start(api, c, { silenceLaterMs: 1000 });
    await settle();
    const es = FakeES.all[0];
    es.send(frame(0), 3);
    es.send({ type: "heartbeat" });
    await vi.advanceTimersByTimeAsync(1001); // goes quiet, falls back to polling
    expect(api.calls.at(-1)?.url).toBe("/flipbook/J/events?after=4&wait=20");
  });
});

describe("polling fallback", () => {
  it("polls the same job when a buffering proxy holds the stream back", async () => {
    const api = fakeApi({
      poll: [
        () => json({ events: [{ type: "heartbeat" }, frame(0)], next: 2, done: false }),
        () => json({ events: [frame(1), DONE], next: 4, done: true }),
      ],
    });
    const c = collect();
    start(api, c);
    await settle();
    expect(c.events).toEqual([]); // the proxy delivered nothing
    await vi.advanceTimersByTimeAsync(5001); // silenceFirstMs
    await vi.advanceTimersByTimeAsync(10);
    expect(FakeES.all[0].closed).toBe(true);
    expect(c.transports).toContain("poll");
    expect(c.events.map((e) => e.type)).toEqual(["heartbeat", "frame", "frame", "done"]);
    const polls = api.calls.filter((x) => x.url.includes("/events")).map((x) => x.url);
    expect(polls).toEqual(["/flipbook/J/events?after=0&wait=20", "/flipbook/J/events?after=2&wait=20"]);
    expect(c.transports.at(-1)).toBe("closed");
  });

  it("resumes polling after the last event the stream delivered", async () => {
    const api = fakeApi({ poll: [() => json({ events: [DONE], next: 3, done: true })] });
    const c = collect();
    start(api, c, { silenceLaterMs: 2000 });
    await settle();
    FakeES.all[0].send(frame(0), 0);
    FakeES.all[0].send(frame(1), 1);
    await vi.advanceTimersByTimeAsync(2001);
    await vi.advanceTimersByTimeAsync(10);
    expect(api.calls.at(-1)?.url).toBe("/flipbook/J/events?after=2&wait=20");
    expect(c.events.map((e) => e.type)).toEqual(["frame", "frame", "done"]);
  });

  it("polls when the browser has no EventSource at all", async () => {
    const api = fakeApi({ poll: [() => json({ events: [DONE], next: 1, done: true })] });
    const c = collect();
    startFlipbook("ra=1&dec=1", c.handlers as never, { fetchFn: api.fetchFn, EventSourceCtor: undefined as never });
    (globalThis as { EventSource?: unknown }).EventSource = undefined;
    await vi.advanceTimersByTimeAsync(10);
    expect(c.events.at(-1)?.type).toBe("done");
  });

  it("polls when EventSource gives up reconnecting", async () => {
    const api = fakeApi({ poll: [() => json({ events: [DONE], next: 1, done: true })] });
    const c = collect();
    start(api, c);
    await settle();
    FakeES.all[0].fail();
    await vi.advanceTimersByTimeAsync(10);
    expect(c.events.at(-1)?.type).toBe("done");
  });

  it("retries a failing poll with backoff, then gives up with a clear error", async () => {
    const boom = () => {
      throw new Error("network down");
    };
    const api = fakeApi({ poll: [boom, boom, boom, boom, boom] });
    const c = collect();
    start(api, c, { silenceFirstMs: 100 });
    await vi.advanceTimersByTimeAsync(100 + 500 + 1000 + 2000 + 4000 + 50);
    const last = c.events.at(-1);
    expect(last?.type === "error" && last.code).toBe("timeout");
    expect(api.calls.filter((x) => x.url.includes("/events")).length).toBe(5);
  });

  it("recovers when a poll fails once and then works", async () => {
    const api = fakeApi({
      poll: [
        () => {
          throw new Error("blip");
        },
        () => json({ events: [DONE], next: 1, done: true }),
      ],
    });
    const c = collect();
    start(api, c, { silenceFirstMs: 100 });
    await vi.advanceTimersByTimeAsync(100 + 500 + 50);
    expect(c.events.at(-1)?.type).toBe("done");
  });

  it("an expired job (404) is a clear error, not a retry loop", async () => {
    const api = fakeApi({ poll: [() => json({ code: "internal", message: "Unknown or expired job." }, 404)] });
    const c = collect();
    start(api, c, { silenceFirstMs: 100 });
    await vi.advanceTimersByTimeAsync(200);
    const last = c.events.at(-1);
    expect(last?.type === "error" && last.message).toBe("Unknown or expired job.");
  });
});

describe("starting the job fails", () => {
  it("a 422 from the server becomes an error event with the server's words", async () => {
    const api = fakeApi({ start: () => json({ code: "bad_coords", message: "Invalid parameters (ra: too big)." }, 422) });
    const c = collect();
    start(api, c);
    await settle();
    expect(c.events).toEqual([{ type: "error", code: "bad_coords", message: "Invalid parameters (ra: too big).", retryable: false }]);
    expect(FakeES.all).toEqual([]);
    expect(c.transports.at(-1)).toBe("closed");
  });

  it("a 5xx is retryable, and an unknown code falls back to internal", async () => {
    const api = fakeApi({ start: () => json({ code: "wat", message: "Broken." }, 503) });
    const c = collect();
    start(api, c);
    await settle();
    expect(c.events[0]).toMatchObject({ type: "error", code: "internal", retryable: true });
  });

  it("a bodyless 502 (a proxy with no server behind it) says the live search is not answering", async () => {
    const api = fakeApi({ start: () => new Response("Bad Gateway", { status: 502 }) });
    const c = collect();
    start(api, c);
    await settle();
    expect(c.events[0]).toMatchObject({ type: "error", retryable: true });
    expect((c.events[0] as { message: string }).message).toMatch(/not answering/);
  });

  it("an unreachable server says so", async () => {
    const api = fakeApi({
      start: () => {
        throw new TypeError("Failed to fetch");
      },
    });
    const c = collect();
    start(api, c);
    await settle();
    expect(c.events[0]).toMatchObject({ type: "error", code: "timeout", retryable: true });
    expect((c.events[0] as { message: string }).message).toMatch(/reach the server/);
  });
});

describe("cancel", () => {
  it("closes the stream without cancelling the shared job on the server", async () => {
    const api = fakeApi();
    const c = collect();
    const run = start(api, c);
    await settle();
    run.cancel();
    expect(FakeES.all[0].closed).toBe(true);
    expect(api.calls.some((x) => x.method === "DELETE")).toBe(false);
    FakeES.all[0].send(frame(0), 0); // late events from a closed stream are dropped
    expect(c.events).toEqual([]);
    expect(c.transports.at(-1)).toBe("closed");
  });

  it("cancelling before the job even started opens no stream", async () => {
    const api = fakeApi();
    const c = collect();
    const run = start(api, c);
    run.cancel();
    await settle();
    expect(FakeES.all).toEqual([]);
  });

  it("a stopped run does not poll on after cancel", async () => {
    const api = fakeApi({ poll: [() => json({ events: [frame(0)], next: 1, done: false })] });
    const c = collect();
    const run = start(api, c, { silenceFirstMs: 100 });
    await vi.advanceTimersByTimeAsync(110);
    run.cancel();
    await vi.advanceTimersByTimeAsync(5000);
    expect(api.calls.filter((x) => x.url.includes("/events")).length).toBeLessThanOrEqual(2);
  });
});
