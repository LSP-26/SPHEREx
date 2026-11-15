import { useState, type FormEvent } from "react";
import { findExample, type Example } from "../lib/examples";
import { search } from "../lib/search";
import type { Showcase } from "../lib/showcases";
import { pictureSizePx } from "../lib/sky";

export type Go =
  | { kind: "example"; example: Example }
  | { kind: "live"; ra: number; dec: number; name: string | null; fov: number; note?: string };

export const DEFAULT_FOV = 5;
export const MIN_FOV = 1;
export const MAX_FOV = 120; // the server's limit
const PRESETS = [2, 5, 10, 30, 60, 120];

/** Megabytes read from the archive for a typical field (about 24 frames). The server reads full
 *  detector rows of four planes, so the cost grows with the field's height. */
export function estimateMb(fovArcmin: number, frames = 24): number {
  return (4 * pictureSizePx(fovArcmin) * 2040 * 4 * frames) / 1e6;
}

/** The field on screen, so the box can be re-run with another field of view without retyping. */
export interface Current {
  ra: number;
  dec: number;
  name: string | null;
  fov: number;
  /** Set when the field on screen is a recorded example. */
  example?: Example;
}

/** What is in the box when a field is on screen: its name, or its position in degrees. */
export function currentText(c: Current): string {
  return c.name ?? `${c.ra.toFixed(5)} ${c.dec >= 0 ? "+" : ""}${c.dec.toFixed(5)}`;
}

/** Decides where a search goes without asking SIMBAD, when it can. Null means: look it up. */
export function planSearch(text: string, fov: number, current?: Current, showcases: Showcase[] = []): Go | null {
  const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();
  // the same target as on screen: keep its position, change only the field of view
  if (current && same(text, currentText(current))) {
    if (current.example && fov === current.fov) return { kind: "example", example: current.example };
    return { kind: "live", ra: current.ra, dec: current.dec, name: current.name, fov };
  }
  // one of the recorded examples: its recording, or its stored position at another field of view
  const ex = findExample(text);
  if (ex) {
    const s = showcases.find((x) => x.key === ex.key);
    if (fov === DEFAULT_FOV || (s && fov === s.fov_arcmin)) return { kind: "example", example: ex };
    if (s) return { kind: "live", ra: s.ra, dec: s.dec, name: ex.name, fov };
  }
  return null;
}

interface Props {
  current?: Current;
  showcases?: Showcase[];
  large?: boolean;
  onGo(go: Go): void;
}

export function SearchBar({ current, showcases = [], large = false, onGo }: Props) {
  const [text, setText] = useState(current ? currentText(current) : "");
  const [fov, setFov] = useState(String(current?.fov ?? DEFAULT_FOV));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fovNum = Number(fov);
  const fovOk = fov.trim() !== "" && Number.isFinite(fovNum) && fovNum >= MIN_FOV && fovNum <= MAX_FOV;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!fovOk) return setError(`The field of view must be between ${MIN_FOV}′ and ${MAX_FOV}′ (arcminutes).`);
    const planned = planSearch(text, fovNum, current, showcases);
    if (planned) return onGo(planned);
    setBusy(true);
    const r = await search(text);
    setBusy(false);
    if (!r.ok) return setError(r.error);
    onGo({ kind: "live", ra: r.ra, dec: r.dec, name: r.name, fov: fovNum, note: r.note });
  }

  const field = "bg-panel text-ink focus:outline-none";
  const pad = large ? "py-3.5" : "py-1.5";
  const text_ = large ? "text-base" : "text-sm";
  const ring = large ? "border-2 shadow-[0_0_0_4px_var(--color-accent-soft)]" : "border";
  return (
    <form onSubmit={submit} noValidate role="search" aria-label="Find a patch of sky" className="w-full">
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-0 flex-[1_1_18rem]">
          <span className="mb-1 block text-xs text-muted">Object name or coordinates (RA, Dec)</span>
          <input value={text} onChange={(e) => setText(e.target.value)} autoComplete="off" spellCheck={false}
            placeholder="e.g. Barnard's Star · 269.452 4.693 · 17h57m48.5s +04d41m36s"
            className={`${field} ${ring} w-full rounded-sm ${large ? "border-accent" : "border-rule-strong"} px-3 ${pad} ${text_} focus:border-accent`} />
        </label>
        <label className="w-40">
          <span className="mb-1 block text-xs text-muted">Field of view (1–120 arcmin)</span>
          <span className="flex items-center">
            <input type="number" inputMode="decimal" min={MIN_FOV} max={MAX_FOV} step="any" value={fov}
              onChange={(e) => setFov(e.target.value)} list="fov-presets" aria-invalid={!fovOk}
              aria-describedby="fov-help"
              className={`${field} border tabular w-full rounded-l-sm px-2.5 ${pad} ${text_} ${fovOk ? "border-rule-strong focus:border-accent" : "border-danger"}`} />
            <span className={`rounded-r-sm border border-l-0 border-rule-strong bg-paper px-2 ${pad} ${text_} text-muted`}>′</span>
          </span>
          <datalist id="fov-presets">{PRESETS.map((p) => <option key={p} value={p} />)}</datalist>
        </label>
        <button type="submit" disabled={busy || text.trim() === ""}
          className={`rounded-sm bg-accent px-6 ${pad} ${text_} font-semibold text-paper hover:bg-ink disabled:opacity-40`}>
          {busy ? "Searching…" : "Search"}
        </button>
      </div>
      <p id="fov-help" className={`mt-1 text-xs ${fovOk ? "text-faint" : "text-danger"}`}>
        {fovOk
          ? `${pictureSizePx(fovNum)} × ${pictureSizePx(fovNum)} pixels of 6.15″; about ${Math.round(estimateMb(fovNum))} MB read from the archive.` +
            (fovNum > 30 ? " Large fields take a minute or more." : "")
          : `Field of view must be between ${MIN_FOV}′ and ${MAX_FOV}′.`}
      </p>
      {error && <p role="alert" className="mt-1 text-sm text-danger">{error}</p>}
    </form>
  );
}
