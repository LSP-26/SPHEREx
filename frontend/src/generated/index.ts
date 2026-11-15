// Re-exports the generated types under the names the app uses. The generated files each carry
// their own copy of shared types (json-schema-to-typescript works one schema at a time), so only
// the top-level names are exported here.
export type { FlipbookRequest } from "./FlipbookRequest";
export type { FrameLabel } from "./FrameLabel";
export type { FrameRef } from "./FrameRef";
export type { JobHandle } from "./JobHandle";
export type { PollResponse } from "./PollResponse";
export type {
  DoneEvent,
  ErrorCode,
  ErrorEvent,
  Event,
  FrameEvent,
  HeartbeatEvent,
  PlanEvent,
  ProgressEvent,
  Stage,
} from "./Event";
