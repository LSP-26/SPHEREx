// Starts a flipbook job on the API and delivers its events, by server-sent events when that
// works and by polling when it does not.
//
//   POST /flipbook?<query>             -> JobHandle (plain JSON, never held back by a proxy)
//   EventSource(handle.stream_url)     -> events as they happen
//   GET handle.poll_url?after=N&wait=S -> the same events, if the stream stays silent
//
// Why both: some gateways buffer a streamed response and deliver it in one late burst. The
// server sends a heartbeat event straight away, so if nothing arrives within silenceFirstMs the
// stream is being held back, and we poll the same job instead, resuming at the first event we
// have not seen. A stream that goes quiet later (silenceLaterMs, longer than the server's
// 15 s heartbeat) is treated the same way.
import type { JobHandle, PollResponse } from "../generated";
import { asErrorCode, errorEvent, isTerminal, parseEvent, type FlipbookEvent } from "./events";
import type { Transport } from "./reducer";

export interface EventSourceLike {
  onmessage: ((e: { data: string; lastEventId: string }) => void) | null;
  onerror: ((e: unknown) => void) | null;
  readyState: number;
  close(): void;
}

export interface StreamDeps {
  fetchFn?: typeof fetch;
  EventSourceCtor?: new (url: string) => EventSourceLike;
  /** Prefix for API URLs when the page and the API are on different hosts. Default: same host. */
  apiBase?: string;
  silenceFirstMs?: number;
  silenceLaterMs?: number;
  pollWaitS?: number;
}

export interface Handlers {
  onEvent(e: FlipbookEvent): void;
  onTransport?(t: Transport): void;
}

const EVENT_SOURCE_CLOSED = 2;

export function startFlipbook(query: string, h: Handlers, deps: StreamDeps = {}) {
  const fetchFn = deps.fetchFn ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
  const ES = deps.EventSourceCtor ?? (globalThis as { EventSource?: StreamDeps["EventSourceCtor"] }).EventSource;
  const base = deps.apiBase ?? "";
  const silenceFirst = deps.silenceFirstMs ?? 5000;
  const silenceLater = deps.silenceLaterMs ?? 20000;
  const pollWait = deps.pollWaitS ?? 20;

  let closed = false;
  let next = 0; // index of the first event we have not seen
  let es: EventSourceLike | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let handle: JobHandle | null = null;
  let streaming = false;
  const abort = new AbortController();

  const clearTimer = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  };
  const stop = () => {
    closed = true;
    clearTimer();
    es?.close();
    es = null;
    abort.abort();
    h.onTransport?.("closed");
  };
  // Frame URLs come back as paths on the API host. Make them work from another origin.
  const absolute = (ev: FlipbookEvent): FlipbookEvent =>
    base && ev.type === "frame" && ev.png_url.startsWith("/") ? { ...ev, png_url: base + ev.png_url } : ev;
  const emit = (ev: FlipbookEvent) => {
    if (closed) return;
    h.onEvent(absolute(ev));
    if (isTerminal(ev)) stop();
  };
  const fail = (code: Parameters<typeof asErrorCode>[0], message: string, retryable: boolean) => {
    emit(errorEvent(asErrorCode(code), message, retryable));
    if (!closed) stop();
  };

  // ---------------------------------------------------------------- polling
  async function poll(failures = 0): Promise<void> {
    if (closed || !handle) return;
    try {
      const r = await fetchFn(`${base}${handle.poll_url}?after=${next}&wait=${pollWait}`, {
        signal: abort.signal,
      });
      if (r.status === 404) {
        const body = await safeJson(r);
        return fail("internal", body?.message ?? "This job has expired. Start it again.", false);
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const body = (await r.json()) as PollResponse;
      for (const raw of body.events) {
        const ev = parseEvent(raw);
        if (ev) emit(ev);
        if (closed) return;
      }
      next = body.next;
      if (body.done) return stop();
      timer = setTimeout(() => void poll(), 0);
    } catch {
      if (closed) return;
      if (failures >= 4) return fail("timeout", "Lost contact with the server. Try again.", true);
      timer = setTimeout(() => void poll(failures + 1), 500 * 2 ** failures);
    }
  }

  function fallBackToPolling() {
    clearTimer();
    es?.close();
    es = null;
    if (closed) return;
    h.onTransport?.("poll");
    void poll();
  }

  // ---------------------------------------------------------------- streaming
  function openStream() {
    if (!handle) return;
    if (!ES) return fallBackToPolling();
    const source = new ES(base + handle.stream_url);
    es = source;
    h.onTransport?.("connecting");
    timer = setTimeout(fallBackToPolling, silenceFirst);
    source.onmessage = (m) => {
      if (closed) return;
      clearTimer();
      timer = setTimeout(fallBackToPolling, silenceLater);
      if (!streaming) {
        streaming = true;
        h.onTransport?.("stream");
      }
      let ev: FlipbookEvent | null;
      try {
        ev = parseEvent(JSON.parse(m.data));
      } catch {
        return;
      }
      if (!ev) return;
      if (m.lastEventId !== "") {
        const index = Number(m.lastEventId);
        if (index < next) return; // already seen (a reconnect replayed it)
        next = index + 1;
      }
      emit(ev);
    };
    source.onerror = () => {
      // EventSource retries by itself while it is CONNECTING. Once it gives up, poll instead.
      if (source.readyState === EVENT_SOURCE_CLOSED) fallBackToPolling();
    };
  }

  // ---------------------------------------------------------------- start
  void (async () => {
    h.onTransport?.("connecting");
    let res: Response;
    try {
      res = await fetchFn(`${base}/flipbook?${query}`, { method: "POST", signal: abort.signal });
    } catch {
      if (closed) return;
      return fail("timeout", "Could not reach the server, so live search is offline. Check your connection and try again.", true);
    }
    if (closed) return;
    if (!res.ok) {
      const body = await safeJson(res);
      const down = res.status >= 502 && res.status <= 504;   // a proxy with nothing behind it
      return fail(body?.code,
        body?.message ?? (down ? "The live search server is not answering. Try again in a moment."
          : `The server answered ${res.status}.`),
        res.status >= 500);
    }
    try {
      handle = (await res.json()) as JobHandle;
    } catch {
      return fail("internal", "The server sent an answer we could not read.", true);
    }
    openStream();
  })();

  return {
    /** Stop listening. The job keeps running on the server for other viewers and is cancelled
     *  there once nobody has watched it for a while, so this deliberately does not DELETE it. */
    cancel() {
      if (!closed) stop();
    },
  };
}

async function safeJson(r: Response): Promise<{ code?: string; message?: string } | null> {
  try {
    return (await r.json()) as { code?: string; message?: string };
  } catch {
    return null;
  }
}
