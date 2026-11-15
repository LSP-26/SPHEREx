import { API_BASE } from "../lib/apiBase";
import { useCallback, useEffect, useReducer, useRef } from "react";
import { initialState, reducer } from "../lib/reducer";
import { replayStatic } from "../lib/staticSource";
import { startFlipbook, type StreamDeps } from "../lib/stream";


type Run = { cancel(): void };

/** Runs one flipbook at a time: live from the API, or replayed from a recorded events file. */
export function useFlipbook(deps: StreamDeps = {}) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const run = useRef<Run | null>(null);
  const begin = useCallback((make: (h: Parameters<typeof startFlipbook>[1]) => Run) => {
    run.current?.cancel();
    dispatch({ type: "start" });
    run.current = make({
      onEvent: (event) => dispatch({ type: "event", event }),
      onTransport: (transport) => dispatch({ type: "transport", transport }),
    });
  }, []);

  /** query: the URL query string of the flipbook (see lib/urlState.toQuery). */
  const start = useCallback(
    (query: string) => begin((h) => startFlipbook(query, h, { apiBase: API_BASE, ...deps })),
    // deps are fixed for the life of the component (tests inject them once)
    [begin],
  );
  const startStatic = useCallback((url: string) => begin((h) => replayStatic(url, h)), [begin]);
  const stop = useCallback(() => {
    run.current?.cancel();
    run.current = null;
    dispatch({ type: "reset" });
  }, []);

  useEffect(() => () => run.current?.cancel(), []);
  return { state, start, startStatic, stop };
}
