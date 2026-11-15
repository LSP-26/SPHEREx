import { useEffect, useState } from "react";
import { parseManifest, SHOWCASE_BASE, type Showcase } from "../lib/showcases";

/** The gallery's showcases from /showcases/manifest.json, or [] if there are none (the gallery
 *  then shows nothing). Like useTargets, it does not fetch under vitest unless `enabled`. */
export function useShowcases(opts: { fetchFn?: typeof fetch; enabled?: boolean } = {}): Showcase[] {
  const enabled = opts.enabled ?? import.meta.env.MODE !== "test";
  const [showcases, setShowcases] = useState<Showcase[]>([]);

  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    const fetchFn = opts.fetchFn ?? ((...a: Parameters<typeof fetch>) => fetch(...a));
    void (async () => {
      try {
        const r = await fetchFn(`${SHOWCASE_BASE}manifest.json`, { signal: abort.signal });
        if (r.ok) setShowcases(parseManifest(await r.json()));
      } catch {
        // no showcases: the gallery stays hidden
      }
    })();
    return () => abort.abort();
  }, [enabled]);

  return showcases;
}
