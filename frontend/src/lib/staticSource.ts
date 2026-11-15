// Replays a recorded flipbook: a JSON file holding the events the live API sent (the showcases
// are built by running the live job and saving its events, so live and static cannot drift).
// The file is either an array of events or { events: [...] }. Frame URLs inside it are resolved
// relative to the file, so a showcase folder can be served from any static host or CDN.
import { isTerminal, parseEvent, type FlipbookEvent } from "./events";
import type { Handlers } from "./stream";

export interface StaticOptions {
  fetchFn?: typeof fetch;
  /** Pause between events, to imitate a live stream. Default 0: show everything at once. */
  delayMs?: number;
}

export function replayStatic(fileUrl: string, h: Handlers, opts: StaticOptions = {}) {
  const fetchFn = opts.fetchFn ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
  let cancelled = false;

  void (async () => {
    h.onTransport?.("connecting");
    const fail = (message: string) =>
      h.onEvent({ type: "error", code: "internal", message, retryable: false });
    let events: unknown[];
    try {
      const res = await fetchFn(fileUrl);
      if (!res.ok) return fail(`Could not load the recorded flipbook (HTTP ${res.status}).`);
      const body = (await res.json()) as unknown;
      events = Array.isArray(body) ? body : ((body as { events?: unknown[] }).events ?? []);
    } catch {
      return fail("Could not load the recorded flipbook.");
    }
    const base = new URL(fileUrl, globalThis.location?.href ?? "http://localhost/");
    h.onTransport?.("stream");
    for (const raw of events) {
      if (cancelled) return;
      const ev = parseEvent(raw);
      if (!ev) continue;
      h.onEvent(resolveFrame(ev, base));
      if (isTerminal(ev)) break;
      if (opts.delayMs) await new Promise((r) => setTimeout(r, opts.delayMs));
    }
    h.onTransport?.("closed");
  })();

  return {
    cancel() {
      cancelled = true;
    },
  };
}

function resolveFrame(ev: FlipbookEvent, base: URL): FlipbookEvent {
  return ev.type === "frame" ? { ...ev, png_url: new URL(ev.png_url, base).href } : ev;
}
