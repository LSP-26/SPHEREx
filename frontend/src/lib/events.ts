import type {
  DoneEvent,
  ErrorCode,
  ErrorEvent,
  FrameEvent,
  HeartbeatEvent,
  PlanEvent,
  ProgressEvent,
} from "../generated";

// The schema marks every field that has a default as optional, including `type`, which would
// stop TypeScript narrowing on it. The server always sends `type`, so require it here.
type Tagged<T extends { type?: string }> = T & { type: NonNullable<T["type"]> };

export type FlipbookEvent =
  | Tagged<PlanEvent>
  | Tagged<ProgressEvent>
  | Tagged<FrameEvent>
  | Tagged<HeartbeatEvent>
  | Tagged<DoneEvent>
  | Tagged<ErrorEvent>;

const TYPES = new Set(["plan", "progress", "frame", "heartbeat", "done", "error"]);
const ERROR_CODES = new Set<string>([
  "bad_coords", "no_coverage", "irsa_busy", "irsa_down", "s3_down", "timeout", "cancelled",
  "internal",
]);

/** A server event, or null for anything we do not know (a newer server may add event types). */
export function parseEvent(raw: unknown): FlipbookEvent | null {
  if (typeof raw !== "object" || raw === null) return null;
  const type = (raw as { type?: unknown }).type;
  if (typeof type !== "string" || !TYPES.has(type)) return null;
  return raw as FlipbookEvent;
}

export function isTerminal(e: FlipbookEvent): e is Tagged<DoneEvent> | Tagged<ErrorEvent> {
  return e.type === "done" || e.type === "error";
}

export function errorEvent(code: ErrorCode, message: string, retryable: boolean): FlipbookEvent {
  return { type: "error", code, message, retryable };
}

export function asErrorCode(value: unknown, fallback: ErrorCode = "internal"): ErrorCode {
  return typeof value === "string" && ERROR_CODES.has(value) ? (value as ErrorCode) : fallback;
}
