import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { usePixels } from "../hooks/usePixels";
import { API_BASE } from "../lib/apiBase";
import type { Example } from "../lib/examples";
import { fetchFlags, hasFlags, type FlagFetch } from "../lib/flags";
import { fetchLightcurve, matchLightcurve, type Lightcurve } from "../lib/lightcurve";
import {
  BANDS, COLOUR_CHANNELS, bandWavelength, colourPasses, dayMonthYear, defaultBand, looksByBand, monthYear, yearsBetween,
} from "../lib/looks";
import { fetchOverlay, type OverlayData } from "../lib/overlay";
import type { BandChoice, Mode } from "../lib/page";
import { asRgba, composite, difference, meanOf, type Gray } from "../lib/pixels";
import type { FlipbookState, Slot } from "../lib/reducer";
import { asteroidsAt, fetchAsteroids, type Asteroid } from "../lib/skybot";
import { formatDec, formatRa, skyToPicture, type Field } from "../lib/sky";
import {
  AsteroidSection, FlagSection, InspectSection, MoversSection, PhotometrySection, Section, ShareSection, Swatch, buttonClass, type AsteroidState,
} from "./Panels";
import { MARK, SkyView } from "./SkyView";

export interface Target {
  ra: number;
  dec: number;
  fov: number;
  name: string | null;
  note?: string;
}

interface Props {
  state: FlipbookState;
  target: Target;
  example?: Example;
  overlayUrl?: string;
  /** Recorded flag layers of an example (flags.json); live fields ask the server. */
  flagsUrl?: string;
  live: boolean;
  band: BandChoice | null;
  mode: Mode;
  embed: boolean;
  shareUrl: string;
  fullPageUrl: string;
  onView(band: BandChoice | null, mode: Mode): void;
  onRetry?(): void;
}

interface Step {
  t: number;
  date: string;
  frames: Slot[];
  channels?: { r: Slot[]; g: Slot[]; b: Slot[] };
}

const SPEEDS = [0.5, 1, 2, 4];

const chip = (on: boolean) =>
  `tabular rounded-sm border px-2 py-1 text-[13px] disabled:opacity-35 ${on ? "border-ink bg-ink text-paper" : "border-rule-strong bg-panel text-ink hover:border-accent"}`;

export function Explorer(p: Props) {
  const { slots } = p.state;
  const byBand = useMemo(() => looksByBand(slots), [slots]);
  const colour = useMemo(() => colourPasses(slots), [slots]);
  const auto = defaultBand(byBand);
  const wanted = p.band ?? p.example?.band ?? auto;
  const band: BandChoice | null =
    wanted === "colour" ? (colour.length > 0 ? "colour" : auto) : wanted != null && byBand.has(wanted) ? wanted : auto;
  const steps: Step[] = useMemo(() => {
    if (band === "colour") {
      return colour.map((pass) => {
        const pick = (ds: readonly number[]) => ds.flatMap((d) => (pass.byDetector.get(d) ? [pass.byDetector.get(d)!.frame] : []));
        const channels = { r: pick(COLOUR_CHANNELS.r), g: pick(COLOUR_CHANNELS.g), b: pick(COLOUR_CHANNELS.b) };
        return { t: pass.start, date: monthYear(pass.start), frames: [...channels.r, ...channels.g, ...channels.b], channels };
      });
    }
    return (band == null ? [] : byBand.get(band) ?? []).map((l) => ({ t: l.t, date: dayMonthYear(l.t), frames: [l.frame] }));
  }, [band, byBand, colour]);
  const mode: Mode = band === "colour" || steps.length < 2 ? "blink" : p.mode;

  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [fps, setFps] = useState(1);
  const [showStars, setShowStars] = useState(true);
  const [showRocks, setShowRocks] = useState(false);
  const [showFlags, setShowFlags] = useState(false);
  const [picked, setPicked] = useState<{ u: number; v: number } | null>(null);
  const [measured, setMeasured] = useState<Lightcurve | null>(null);
  const at = Math.min(index, Math.max(0, steps.length - 1));
  const step = steps[at];

  const fieldKey = `${p.target.ra},${p.target.dec},${p.target.fov}`;
  useEffect(() => { setIndex(0); setPicked(null); setMeasured(null); setPlaying(true); setShowRocks(false); setShowFlags(false); }, [fieldKey]);

  useEffect(() => {
    if (!playing || steps.length < 2) return;
    const id = setInterval(() => setIndex((i) => (i + 1) % steps.length), 1000 / fps);
    return () => clearInterval(id);
  }, [playing, steps.length, fps]);

  const urls = useMemo(() => [...new Set(steps.flatMap((s) => s.frames.map((f) => f.png_url)))], [steps]);
  const grays = usePixels(urls);
  const gray = (s: Slot | undefined): Gray | null => (s ? grays[urls.indexOf(s.png_url)] ?? null : null);

  const ref = steps.length < 2 || !step ? null
    : Math.abs(step.t - steps[0].t) >= Math.abs(step.t - steps[steps.length - 1].t) ? steps[0] : steps[steps.length - 1];

  const picture = useMemo(() => {
    if (!step) return null;
    if (band === "colour" && step.channels) {
      const ch = (list: Slot[]) => meanOf(list.map(gray).filter((g): g is Gray => g !== null));
      const r = ch(step.channels.r), g = ch(step.channels.g), b = ch(step.channels.b);
      const any = r ?? g ?? b;
      if (!any || [r, g, b].some((c) => c && (c.width !== any.width || c.height !== any.height))) return null;
      return { rgba: composite({ r, g, b }), w: any.width, h: any.height };
    }
    const cur = gray(step.frames[0]);
    if (!cur) return null;
    if (mode === "diff" && ref) {
      const other = gray(ref.frames[0]);
      if (!other || other.width !== cur.width || other.height !== cur.height) return null;
      return { rgba: difference(cur, other), w: cur.width, h: cur.height };
    }
    return { rgba: asRgba(cur), w: cur.width, h: cur.height };
    // gray() reads grays and urls, which are listed
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, band, mode, ref, grays, urls]);

  // Gaia stars: saved for the examples, asked from the server otherwise
  const [overlay, setOverlay] = useState<OverlayData | null>(null);
  useEffect(() => {
    setOverlay(null);
    let alive = true;
    void fetchOverlay({ ra: p.target.ra, dec: p.target.dec, fov_arcmin: p.target.fov, staticUrl: p.overlayUrl }, API_BASE)
      .then((r) => alive && r.ok && setOverlay(r.data));
    return () => { alive = false; };
    // fieldKey stands for the target
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fieldKey, p.overlayUrl]);

  // known asteroids at every frame date, asked from SkyBoT the first time the layer is switched on
  const [rocks, setRocks] = useState<{ state: AsteroidState; byEpoch?: Map<number, Asteroid[]> } | null>(null);
  const allTimes = useMemo(() => slots.flatMap((s) => (s ? [s.label.t_mid_mjd] : [])), [slots]);
  useEffect(() => { setRocks(null); }, [fieldKey]);
  useEffect(() => {
    if (!showRocks || rocks || allTimes.length === 0) return;
    setRocks({ state: { status: "busy" } });
    void fetchAsteroids({ ra: p.target.ra, dec: p.target.dec, fov_arcmin: p.target.fov }, allTimes).then((r) => {
      if (!r.ok) return setRocks({ state: { status: "error", error: r.error } });
      const names = new Set([...r.byEpoch.values()].flat().map((a) => a.name));
      setRocks({ state: { status: "ok", total: names.size, missing: r.missing }, byEpoch: r.byEpoch });
    });
  }, [showRocks, rocks, allTimes, p.target.ra, p.target.dec, p.target.fov]);

  // pipeline flags of the frame on show, asked the first time the layer is switched on (one frame at a time)
  const [flagMaps, setFlagMaps] = useState<Map<string, FlagFetch | "busy">>(new Map());
  useEffect(() => { setFlagMaps(new Map()); }, [fieldKey, p.flagsUrl]);
  const flagFrame = step && band !== "colour" ? step.frames[0] : undefined;
  const flagId = flagFrame && mode === "blink" && hasFlags({ stage: flagFrame.stage, label: flagFrame.label }) ? flagFrame.label.frame_id : null;
  const flagCanAsk = !!p.flagsUrl || p.live || API_BASE !== "";
  useEffect(() => {
    if (!showFlags || !flagId || !flagCanAsk || flagMaps.has(flagId)) return;
    setFlagMaps((m) => new Map(m).set(flagId, "busy"));
    void fetchFlags({ ra: p.target.ra, dec: p.target.dec, fov_arcmin: p.target.fov, orient: "north", staticUrl: p.flagsUrl }, flagId, API_BASE)
      .then((r) => setFlagMaps((m) => new Map(m).set(flagId, r)));
    // fieldKey stands for the target
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showFlags, flagId, flagMaps, flagCanAsk, p.flagsUrl]);
  const flagEntry = flagId ? flagMaps.get(flagId) : undefined;
  const flagLayer = showFlags && flagEntry && flagEntry !== "busy" && flagEntry.ok ? flagEntry.data.png_url : null;

  // the light curve built ahead for the variable-star examples
  const [builtLc, setBuiltLc] = useState<Lightcurve | null>(null);
  useEffect(() => {
    setBuiltLc(null);
    const m = matchLightcurve(p.target.ra, p.target.dec);
    if (!m) return;
    let alive = true;
    void fetchLightcurve(m.key).then((r) => alive && r.ok && setBuiltLc(r.data));
    return () => { alive = false; };
    // fieldKey stands for the target
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fieldKey]);

  const size = picture?.w ?? slots.find(Boolean)?.width ?? 0;
  const field: Field = { ra: p.target.ra, dec: p.target.dec, size };
  const snap = (q: { u: number; v: number }) =>
    size > 0
      ? { u: (Math.min(size - 1, Math.max(0, Math.floor(q.u * size))) + 0.5) / size, v: (Math.min(size - 1, Math.max(0, Math.floor(q.v * size))) + 0.5) / size }
      : q;
  const rocksHere = rocks?.byEpoch && step ? asteroidsAt(rocks.byEpoch, step.t) : [];
  const rockMarks = showRocks && size > 0
    ? rocksHere.map((a) => ({ ...skyToPicture(a.ra, a.dec, field), name: a.name, vmag: a.vmag }))
      .filter((a) => a.u >= 0 && a.u <= 1 && a.v >= 0 && a.v <= 1)
    : null;

  const bandLabel = (b: BandChoice | null) => {
    if (b === "colour") return "false colour, all detectors";
    if (b == null) return "";
    const w = bandWavelength(byBand.get(b) ?? []);
    return `detector ${b}, ${w != null ? `${w.toFixed(2)} µm` : ""}`;
  };
  const span = steps.length > 1 ? yearsBetween(steps[0].t, steps[steps.length - 1].t) : 0;
  const choose = (b: BandChoice) => p.onView(b, b === "colour" ? "blink" : p.mode);
  const go = (by: number) => { setPlaying(false); setIndex((i) => (i + by + steps.length) % Math.max(1, steps.length)); };

  function onKey(e: KeyboardEvent) {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
    if (e.key === " ") { e.preventDefault(); setPlaying((x) => !x); }
    else if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); go(-1); }
    else if (e.key === "d" && band !== "colour") p.onView(band, mode === "diff" ? "blink" : "diff");
  }

  const loading = p.state.phase === "connecting" || p.state.phase === "running";
  const progress = p.state.progress;
  const flags = [...new Set(step?.frames.flatMap((f) => f.label.flags_summary ?? []) ?? [])];
  const frame = step?.frames[0];

  const viewer = (
    <div className="space-y-2.5" onKeyDown={onKey}>
      {loading && (
        <div role="status" className="text-xs text-muted">
          <div className="mb-1 h-0.5 bg-rule">
            <div className="h-full bg-accent" style={{ width: `${progress?.total ? (100 * progress.done) / progress.total : 5}%` }} />
          </div>
          Reading SPHEREx frames from IRSA{progress?.total ? `: ${progress.done} of ${progress.total}` : "…"}
        </div>
      )}
      <SkyView
        rgba={picture?.rgba ?? null}
        width={picture?.w ?? 0}
        height={picture?.h ?? 0}
        fallbackSrc={mode === "blink" && band !== "colour" ? frame?.png_url : undefined}
        alt={step ? `SPHEREx, ${bandLabel(band)}, ${step.date}${mode === "diff" ? ", difference" : ""}` : "Waiting for the first frame"}
        stars={showStars && overlay && step ? { data: overlay, t: step.t, maxMag: 15, onlyFast: mode === "diff" } : null}
        asteroids={rockMarks}
        picked={picked}
        onPick={p.embed ? undefined : (q) => setPicked(snap(q))}
      >
        {flagLayer && (
          <img src={flagLayer} alt="Flagged pixels of this frame" data-testid="flag-layer"
            className="pointer-events-none absolute inset-0 h-full w-full opacity-70 [image-rendering:pixelated]" />
        )}
        {step && (
          <div className="tabular pointer-events-none absolute inset-x-0 top-0 flex justify-between bg-black/45 px-2 py-1 text-xs text-white">
            <span>{step.date}{mode === "diff" && ref ? ` − ${ref.date}` : ""}</span>
            <span>{band === "colour" ? "B 0.75–1.6 · G 1.6–3.8 · R 3.8–5.0 µm" : bandLabel(band)}</span>
          </div>
        )}
        {mode === "diff" && step && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-black/45 px-2 py-1 text-xs text-white">
            White: brighter on {step.date}. Black: brighter on {ref?.date}. Grey: unchanged.
          </div>
        )}
      </SkyView>

      {steps.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex">
            <button type="button" onClick={() => go(-1)} aria-label="Previous look" className={`${buttonClass} rounded-r-none`}>◀</button>
            <button type="button" onClick={() => setPlaying((x) => !x)} disabled={steps.length < 2}
              className={`${buttonClass} -ml-px w-16 rounded-none`}>{playing ? "Pause" : "Play"}</button>
            <button type="button" onClick={() => go(1)} aria-label="Next look" className={`${buttonClass} -ml-px rounded-l-none`}>▶</button>
          </div>
          <label className="flex items-center gap-1.5 text-sm text-muted">
            Speed
            <select value={fps} onChange={(e) => setFps(Number(e.target.value))}
              className="rounded-sm border border-rule-strong bg-panel px-2 py-1 text-[13px] text-ink">
              {SPEEDS.map((s) => <option key={s} value={s}>{s} frame{s === 1 ? "" : "s"}/s</option>)}
            </select>
          </label>
          <ol className="flex min-w-0 flex-1 flex-wrap gap-1" aria-label="Looks, oldest first">
            {steps.map((s, i) => (
              <li key={`${s.t}-${i}`} className="shrink-0">
                <button type="button" onClick={() => { setPlaying(false); setIndex(i); }} aria-current={i === at} className={chip(i === at)}>
                  {s.date}
                </button>
              </li>
            ))}
          </ol>
        </div>
      )}

      {(byBand.size > 0 || colour.length > 0) && (
        <div className="flex flex-wrap items-start justify-between gap-x-8 gap-y-3 text-sm">
          <div className="min-w-0 space-y-2.5">
            <div className="flex flex-wrap items-center gap-1.5" role="radiogroup" aria-label="Detector band">
              <span className="w-[4.5rem] text-muted">Band (µm)</span>
              {BANDS.map((b) => {
                const looks = byBand.get(b.detector) ?? [];
                const w = bandWavelength(looks);
                return (
                  <button key={b.detector} type="button" role="radio" aria-checked={band === b.detector} disabled={looks.length === 0}
                    onClick={() => choose(b.detector)} className={chip(band === b.detector)}
                    title={`Detector ${b.detector}: ${b.range[0]}–${b.range[1]} µm, ${looks.length} look${looks.length === 1 ? "" : "s"}`}>
                    D{b.detector} {(w ?? b.range[0]).toFixed(2)}
                  </button>
                );
              })}
              <button type="button" role="radio" aria-checked={band === "colour"} disabled={colour.length === 0} onClick={() => choose("colour")}
                className={chip(band === "colour")} title="False colour from all six detectors, one picture per pass over the sky">
                Colour
              </button>
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <span className="flex items-center gap-1.5" role="radiogroup" aria-label="View">
                <span className="w-[4.5rem] text-muted">View</span>
                {(["blink", "diff"] as const).map((m) => (
                  <button key={m} type="button" role="radio" aria-checked={mode === m} disabled={m === "diff" && (band === "colour" || steps.length < 2)}
                    onClick={() => p.onView(band, m)} className={chip(mode === m)}
                    title={m === "diff" ? "Each look minus the look furthest from it in time (key D)" : "The looks in time order"}>
                    {m === "blink" ? "Blink" : "Difference"}
                  </button>
                ))}
              </span>
            </div>
          </div>
          {!p.embed && (
            <span className="flex shrink-0 flex-col gap-2 text-[15px]" aria-label="Overlays">
              <label className="flex items-start gap-2">
                <input type="checkbox" className="mt-0.5 h-[18px] w-[18px]" checked={showStars} onChange={(e) => setShowStars(e.target.checked)} disabled={!overlay} />
                <span>
                  <Swatch colour={MARK.gaia} /> Gaia stars (G ≤ 15)
                  <br />
                  <Swatch colour={MARK.fast} /> Fast movers
                </span>
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" className="h-[18px] w-[18px]" checked={showRocks} onChange={(e) => setShowRocks(e.target.checked)} />
                <Swatch colour={MARK.asteroid} shape="diamond" /> Known asteroids
              </label>
              <label className="flex items-center gap-2" title="Where the SPHEREx pipeline flagged pixels in this frame">
                <input type="checkbox" className="h-[18px] w-[18px]" checked={showFlags} onChange={(e) => setShowFlags(e.target.checked)} disabled={!flagCanAsk} />
                <Swatch colour="#f97316" shape="box" /> Flagged pixels
              </label>
            </span>
          )}
        </div>
      )}
    </div>
  );

  if (p.embed) {
    return (
      <div className="mx-auto max-w-[min(100%,calc(100vh-9rem))] space-y-2 p-2">
        {viewer}
        <p className="text-xs text-muted">
          {p.target.name ?? `${formatRa(p.target.ra)} ${formatDec(p.target.dec)}`} · SPHEREx data: NASA/IPAC IRSA ·{" "}
          <a href={p.fullPageUrl} target="_blank" rel="noreferrer" className="underline">Open in Tombaugh</a>
        </p>
      </div>
    );
  }

  const title = p.example?.name ?? p.target.name ?? "Field";
  const lc = measured ?? builtLc;
  const currentDetector = typeof band === "number" ? band : null;
  return (
    <>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[minmax(0,1fr)_21rem]">
        <div className="mx-auto w-full max-w-[min(100%,calc(100vh-17.5rem))]">{viewer}</div>
        <aside className="space-y-3 text-sm">
          <div>
            <h2 className="text-xl font-semibold">{title}</h2>
            {p.example && <p className="text-muted">{p.example.kind}</p>}
            <p className="tabular mt-1 font-mono text-xs text-muted">
              {formatRa(p.target.ra)} {formatDec(p.target.dec)} · {p.target.fov}′ field
            </p>
            {p.target.note && <p className="mt-1 text-xs text-muted">{p.target.note}</p>}
            <p className="mt-2 leading-relaxed">
              {p.example?.lookFor ??
                "Step through the looks and watch for anything that shifts, appears or changes brightness. " +
                "In Difference, unchanged sources cancel to grey and a mover leaves a white and black pair."}
            </p>
          </div>

          {frame && step && (
            <Section title="Current frame">
              <dl className="tabular grid grid-cols-[5rem_1fr] gap-y-0.5 text-sm">
                {band === "colour" ? (
                  <><dt className="text-faint">Pass</dt><dd>{step.date} ({step.frames.length} frames)</dd></>
                ) : (
                  <>
                    <dt className="text-faint">Observed</dt><dd>{dayMonthYear(frame.label.t_mid_mjd)}</dd>
                    <dt className="text-faint">Band</dt><dd>D{frame.label.detector}, {frame.label.lambda_um.toFixed(3)} µm</dd>
                    <dt className="text-faint">Release</dt><dd>{frame.label.release} {frame.label.version}</dd>
                    <dt className="text-faint">Frame</dt><dd className="break-all font-mono text-[11px]">{frame.label.frame_id}</dd>
                  </>
                )}
                <dt className="text-faint">Quality</dt><dd>{flags.length ? flags.join("; ") : "no pipeline warnings"}</dd>
              </dl>
              <p className="mt-1 text-xs text-muted">
                {steps.length} {band === "colour" ? (steps.length === 1 ? "pass" : "passes") : steps.length === 1 ? "look" : "looks"}
                {span > 0.05 ? ` over ${span.toFixed(1)} years` : ""}.
              </p>
            </Section>
          )}

          {p.state.phase === "error" && (
            <Section title={slots.some(Boolean) ? "Some frames are missing" : "Could not load this field"}>
              <p>{p.state.error?.message}</p>
              {p.state.error?.retryable && p.onRetry && (
                <button type="button" onClick={p.onRetry} className={`${buttonClass} mt-2`}>Try again</button>
              )}
            </Section>
          )}
          {p.state.phase === "done" && steps.length === 0 && (
            <Section title="No frames"><p>SPHEREx has no usable frames of this position yet.</p></Section>
          )}

          {picked && step ? (
            <InspectSection picked={picked} field={field} t={step.t} overlay={overlay} link={p.shareUrl}
              band={bandLabel(band)} looks={steps.map((s) => s.date)} canMeasure={p.live || API_BASE !== ""}
              onMeasured={setMeasured} onClose={() => setPicked(null)} />
          ) : steps.length > 0 ? (
            <p className="border-t border-rule pt-3 text-xs text-muted">Click a pixel for its coordinates and nearest Gaia star.</p>
          ) : null}
          {overlay && step && <MoversSection data={overlay} t={step.t} onPick={(q) => setPicked(snap(q))} />}
          {showFlags && step && <FlagSection entry={flagId ? flagEntry : undefined} single={flagId !== null} />}
          {showRocks && rocks && <AsteroidSection state={rocks.state} here={rocksHere} />}
          <ShareSection url={p.shareUrl} />
        </aside>
      </div>
      {lc && (
        <PhotometrySection lc={lc} detector={currentDetector}
          title={measured ? "Photometry of the selected pixel" : `Photometry of ${lc.name}`}
          note={measured
            ? "Aperture photometry at a fixed position in every SPHEREx frame covering it (proper motion not applied)."
            : "Aperture photometry of the central star in every SPHEREx frame, measured in advance."} />
      )}
    </>
  );
}
