import type { FrameEvent, PlanEvent, Stage } from "../generated";
import type { FlipbookEvent } from "./events";

export type Transport = "connecting" | "stream" | "poll" | "closed";
export type Phase = "idle" | "connecting" | "running" | "done" | "error";
export type Slot = FrameEvent;

export interface FlipbookState {
  phase: Phase;
  transport: Transport;
  plan: PlanEvent | null;
  progress: { done: number; total: number; stage: Stage } | null;
  /** index = slot number = time order. null while that frame has not arrived. */
  slots: (Slot | null)[];
  error: { code: string; message: string; retryable: boolean } | null;
  done: { n_frames: number; elapsed_s: number } | null;
}

export const initialState: FlipbookState = {
  phase: "idle",
  transport: "closed",
  plan: null,
  progress: null,
  slots: [],
  error: null,
  done: null,
};

export type Action =
  | { type: "start" }
  | { type: "reset" }
  | { type: "event"; event: FlipbookEvent }
  | { type: "transport"; transport: Transport };

export function reducer(state: FlipbookState, action: Action): FlipbookState {
  switch (action.type) {
    case "reset":
      return initialState;
    case "start":
      return { ...initialState, phase: "connecting", transport: "connecting" };
    case "transport":
      return { ...state, transport: action.transport };
    case "event":
      return onEvent(state, action.event);
  }
}

function onEvent(state: FlipbookState, ev: FlipbookEvent): FlipbookState {
  const running: Phase = state.phase === "connecting" ? "running" : state.phase;
  switch (ev.type) {
    case "heartbeat":
      return { ...state, phase: running };
    case "plan":
      return { ...state, phase: "running", plan: ev, slots: Array(ev.n_slots).fill(null) };
    case "progress":
      return { ...state, phase: running, progress: { done: ev.done, total: ev.total, stage: ev.stage } };
    case "frame": {
      const slots = state.slots.slice();
      while (slots.length <= ev.slot) slots.push(null);
      slots[ev.slot] = ev; // a refined frame replaces the preview one in the same slot
      return { ...state, phase: running, slots };
    }
    case "done":
      return { ...state, phase: "done", done: { n_frames: ev.n_frames, elapsed_s: ev.elapsed_s } };
    case "error":
      return {
        ...state,
        phase: "error",
        error: { code: ev.code, message: ev.message, retryable: ev.retryable ?? false },
      };
  }
}

export function filledSlots(state: FlipbookState): number[] {
  return state.slots.flatMap((s, i) => (s ? [i] : []));
}
