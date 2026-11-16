import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../lib/apiBase";
import { dayMonthYear } from "../lib/looks";
import { MOVERS_MAX_FOV_ARCMIN, fetchMovers, rateText, trackLabel, type MoverTrack, type MoversData } from "../lib/movers";
import { Section, Swatch, buttonClass } from "./Panels";
import { MARK } from "./SkyView";

type State =
  | { phase: "idle" }
  | { phase: "busy" }
  | { phase: "error"; error: string }
  | { phase: "done"; data: MoversData };

/** A track that matches a known asteroid or comet is drawn in the asteroid colour; anything
 *  unrecognised is only a candidate and gets its own colour. */
const CANDIDATE = "#7dff8a";
const colourOf = (t: MoverTrack) => (t.known ? MARK.asteroid : CANDIDATE);

/** Search one SPHEREx visit of the field for moving objects (GET /movers) and draw the tracks.
 *  Needs the live server. */
export function MoverSearch({ ra, dec, fov }: { ra: number; dec: number; fov: number }) {
  const [state, setState] = useState<State>({ phase: "idle" });
  const ctl = useRef<AbortController | null>(null);

  // A new field means the old answer no longer applies.
  useEffect(() => {
    ctl.current?.abort();
    setState({ phase: "idle" });
  }, [ra, dec, fov]);
  useEffect(() => () => ctl.current?.abort(), []);

  const run = (visit?: number) => {
    ctl.current?.abort();
    const c = new AbortController();
    ctl.current = c;
    setState({ phase: "busy" });
    void fetchMovers({ ra, dec, fov_arcmin: Math.min(fov, MOVERS_MAX_FOV_ARCMIN), visit }, API_BASE, undefined, c.signal).then((r) => {
      if (c.signal.aborted) return;
      setState(r.ok ? { phase: "done", data: r.data } : { phase: "error", error: r.error });
    });
  };

  return (
    <Section title="Moving objects (experimental)">
      <p className="mb-2 text-xs text-muted">
        Looks in one visit for objects that move in a straight line between SPHEREx bursts, and checks them against known
        asteroids and comets (SkyBoT). Anything it does not recognise is only a candidate, not a discovery.
        {fov > MOVERS_MAX_FOV_ARCMIN && ` Searches the central ${MOVERS_MAX_FOV_ARCMIN}′.`}
      </p>

      {state.phase === "idle" && (
        <button type="button" className={buttonClass} onClick={() => run()}>Search for moving objects</button>
      )}
      {state.phase === "busy" && <p role="status" className="text-sm">Searching every frame of the visit. This can take a minute.</p>}
      {state.phase === "error" && (
        <div role="alert" className="text-sm">
          <p>{state.error}</p>
          <button type="button" className={`${buttonClass} mt-2`} onClick={() => run()}>Try again</button>
        </div>
      )}
      {state.phase === "done" && <Result data={state.data} onVisit={run} />}
    </Section>
  );
}

function Result({ data, onVisit }: { data: MoversData; onVisit(visit: number): void }) {
  const { tracks } = data;
  const candidates = tracks.filter((t) => !t.known).length;
  const alt = tracks.length === 0
    ? "Picture of the field with no moving object found"
    : `Picture of the field with ${tracks.length} moving-object track${tracks.length === 1 ? "" : "s"} drawn on it`;
  return (
    <div className="grid gap-2 text-sm">
      <div className="relative w-full overflow-hidden bg-black">
        <img src={`${API_BASE}${data.png_url}`} alt={alt} className="block w-full" />
        <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden>
          {tracks.map((t, i) => {
            const pts = t.points.map((p) => `${p.u},${p.v}`).join(" ");
            const c = colourOf(t);
            return (
              <g key={i} fill="none" strokeLinejoin="round" strokeLinecap="round">
                <polyline points={pts} stroke="#000" strokeWidth={5} vectorEffect="non-scaling-stroke" />
                <polyline points={pts} stroke={c} strokeWidth={2.5} vectorEffect="non-scaling-stroke" />
              </g>
            );
          })}
        </svg>
        {tracks.map((t, i) => {
          const last = t.points[t.points.length - 1];
          return (
            <span key={i} className="pointer-events-none absolute -translate-y-full whitespace-nowrap bg-black/70 px-1 text-[11px] leading-4 text-white"
              style={{ left: `${Math.min(88, last.u * 100)}%`, top: `${Math.max(8, last.v * 100)}%` }}>
              {trackLabel(t)}
            </span>
          );
        })}
      </div>
      <p className="text-xs text-muted">
        Picture: the strongest brightening at each pixel in any burst of the visit. Lines join the bursts of each track.
      </p>

      {data.visits.length > 1 && (
        <label className="flex items-center gap-2 text-xs">
          Visit
          <select className="rounded-sm border border-rule-strong bg-panel px-1 py-0.5 text-[13px]" value={data.visit}
            onChange={(e) => onVisit(Number(e.target.value))}>
            {data.visits.map((v) => (
              <option key={v.index} value={v.index}>{dayMonthYear(v.t_start_mjd)} · {v.n_frames} frames · {v.n_bursts} bursts</option>
            ))}
          </select>
        </label>
      )}

      {tracks.length === 0 ? (
        <p>
          No moving objects found in this visit ({data.n_detections} detection{data.n_detections === 1 ? "" : "s"} in all). The search
          misses faint objects and ones that pass over a bright star, so this does not mean nothing is there.
        </p>
      ) : (
        <>
          <p>
            {tracks.length} track{tracks.length === 1 ? "" : "s"}: {tracks.length - candidates} matched a known object,
            {" "}{candidates} unmatched candidate{candidates === 1 ? "" : "s"}.
          </p>
          <ul className="grid gap-1">
            {tracks.map((t, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className="mt-0.5 shrink-0"><Swatch colour={colourOf(t)} shape={t.known ? "diamond" : "ring"} /></span>
                <span>
                  <b>{trackLabel(t)}</b>{" "}
                  <span className="tabular text-muted">{rateText(t.rate_arcsec_h)} · PA {Math.round(t.pa_deg)}° · S/N {t.snr} · {t.points.length} bursts</span>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {data.known_objects.length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs">Known objects that crossed the field ({data.known_objects.length})</summary>
          <ul className="mt-1 grid gap-0.5 text-xs">
            {data.known_objects.slice(0, 12).map((o) => (
              <li key={o.name}>
                {o.name}{o.vmag !== null ? ` (V ${o.vmag.toFixed(1)})` : ""}: {o.tracked ? "found by the search" : "not found"}
              </li>
            ))}
          </ul>
        </details>
      )}

      {data.notes.length > 0 && (
        <ul className="grid gap-0.5 text-xs text-muted">{data.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
      )}
    </div>
  );
}
