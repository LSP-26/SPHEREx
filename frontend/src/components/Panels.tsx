import { useEffect, useState, type ReactNode } from "react";
import { API_BASE } from "../lib/apiBase";
import type { FlagFetch } from "../lib/flags";
import { FAST_ARCSEC_YR, describeStar, distanceLy, fastMovers, motionArcsecYr, nearestStar } from "../lib/gaia";
import { measureHere, type Lightcurve } from "../lib/lightcurve";
import { BANDS } from "../lib/looks";
import { starAt, type OverlayData, type OverlayStar } from "../lib/overlay";
import { FEATURES, binByVisit, busiestDetector, spectrumPoints } from "../lib/photometry";
import { reportText, simbadUrl } from "../lib/report";
import type { Asteroid } from "../lib/skybot";
import { formatDec, formatRa, pictureToSky, type Field } from "../lib/sky";
import { LightCurveChart, SpectrumChart } from "./Charts";
import { MARK } from "./SkyView";

export function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="border-t border-rule pt-3" aria-label={title}>
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

export const buttonClass =
  "rounded-sm border border-rule-strong bg-panel px-3 py-1.5 text-sm text-ink hover:border-accent hover:text-accent disabled:opacity-40";

function Copy({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button type="button" className={buttonClass}
      onClick={() => { void navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); }); }}>
      {done ? "Copied" : label}
    </button>
  );
}

export function Swatch({ colour, shape = "ring" }: { colour: string; shape?: "ring" | "diamond" | "box" }) {
  return (
    <svg viewBox="-6 -6 12 12" className="inline-block h-4 w-4 align-[-2px]" aria-hidden>
      {shape === "ring" && <><circle r={4} fill="none" stroke="#000" strokeWidth={3.5} /><circle r={4} fill="none" stroke={colour} strokeWidth={1.75} /></>}
      {shape === "diamond" && (
        <><path d="M0 -4.5L4.5 0L0 4.5L-4.5 0Z" fill="none" stroke="#000" strokeWidth={3.5} /><path d="M0 -4.5L4.5 0L0 4.5L-4.5 0Z" fill="none" stroke={colour} strokeWidth={1.75} /></>
      )}
      {shape === "box" && <rect x={-3.5} y={-3.5} width={7} height={7} fill="none" stroke={colour} strokeWidth={2} />}
    </svg>
  );
}

// ---- known fast-moving stars (Gaia) ------------------------------------------------------------

export function MoversSection({ data, t, onPick }: { data: OverlayData; t: number; onPick(p: { u: number; v: number }): void }) {
  const movers = fastMovers(data).slice(0, 6);
  return (
    <Section title="High proper-motion stars (Gaia DR3)" aside={<Swatch colour={MARK.fast} />}>
      {movers.length === 0 ? (
        <p className="text-sm text-muted">
          None faster than {FAST_ARCSEC_YR}″/yr in this field. Anything that shifts between looks is not a known nearby star.
        </p>
      ) : (
        <table className="tabular w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-faint">
              <th className="font-normal">μ (″/yr)</th><th className="font-normal">Distance</th><th className="font-normal">G</th>
            </tr>
          </thead>
          <tbody>
            {movers.map((s) => (
              <tr key={s.source_id ?? `${s.x},${s.y}`} className="cursor-pointer hover:bg-accent-soft" title="Select this star"
                onClick={() => onPick(starAt(s, data, t))}>
                <td className="py-0.5">{(motionArcsecYr(s) ?? 0).toFixed(2)}</td>
                <td>{distanceLy(s) != null ? `${distanceLy(s)!.toFixed(distanceLy(s)! < 100 ? 1 : 0)} ly` : "—"}</td>
                <td>{s.g_mag?.toFixed(1) ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}

// ---- known asteroids (SkyBoT) ------------------------------------------------------------------

export interface AsteroidState {
  status: "busy" | "ok" | "error";
  error?: string;
  total?: number;
  missing?: number;
}

export function AsteroidSection({ state, here }: { state: AsteroidState; here: Asteroid[] }) {
  return (
    <Section title="Known asteroids and comets (IMCCE SkyBoT)" aside={<Swatch colour={MARK.asteroid} shape="diamond" />}>
      {state.status === "busy" && <p className="text-sm text-muted">Asking SkyBoT for each date…</p>}
      {state.status === "error" && <p role="alert" className="text-sm text-danger">{state.error}</p>}
      {state.status === "ok" && (
        <>
          <p className="text-sm text-muted">
            {state.total === 0
              ? "None in this field at any of these dates."
              : `${state.total} cross the field across these dates; ${here.length} at this one. They move between looks, so each appears in one look only.`}
            {state.missing ? ` SkyBoT did not answer for ${state.missing} date(s).` : ""}
          </p>
          {here.length > 0 && (
            <ul className="tabular mt-1 text-sm">
              {here.slice(0, 8).map((a) => (
                <li key={a.name}>{a.name}{a.vmag != null ? <span className="text-faint"> · V {a.vmag.toFixed(1)}</span> : null}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </Section>
  );
}

// ---- the selected pixel ------------------------------------------------------------------------

interface InspectProps {
  picked: { u: number; v: number };
  field: Field;
  t: number;
  overlay: OverlayData | null;
  link: string;
  band: string;
  looks: string[];
  canMeasure: boolean;
  onMeasured(lc: Lightcurve): void;
  onClose(): void;
}

export function InspectSection({ picked, field, t, overlay, link, band, looks, canMeasure, onMeasured, onClose }: InspectProps) {
  const near: { star: OverlayStar; px: number } | null = overlay ? nearestStar(overlay, picked.u, picked.v, t, 1.0) : null;
  const sky = pictureToSky(picked.u, picked.v, field);
  const pxX = Math.floor(picked.u * field.size);
  const pxY = Math.floor(picked.v * field.size);
  const [measure, setMeasure] = useState<{ state: "idle" | "busy" | "error"; text?: string }>({ state: "idle" });
  const key = `${pxX},${pxY}`;
  useEffect(() => { setMeasure({ state: "idle" }); }, [key]);

  async function run() {
    setMeasure({ state: "busy", text: "Finding frames…" });
    const r = await measureHere({ ra: Number(sky.ra.toFixed(5)), dec: Number(sky.dec.toFixed(5)) }, {
      apiBase: API_BASE,
      onProgress: (d, n) => setMeasure({ state: "busy", text: n ? `Measured ${d} of ${n} frames…` : "Finding frames…" }),
    });
    if (r.ok) { setMeasure({ state: "idle" }); onMeasured(r.data); }
    else setMeasure({ state: "error", text: r.error });
  }

  const known = near ? `Gaia DR3 ${near.star.source_id ?? ""}: ${describeStar(near.star)}` : null;
  return (
    <Section title="Selected pixel" aside={<button type="button" onClick={onClose} className="text-xs text-faint hover:text-ink">Clear</button>}>
      <dl className="tabular grid grid-cols-[5rem_1fr] gap-y-0.5 text-sm">
        <dt className="text-faint">Pixel</dt><dd>x {pxX}, y {pxY}</dd>
        <dt className="text-faint">RA, Dec</dt><dd className="font-mono text-[13px]">{formatRa(sky.ra)} {formatDec(sky.dec)}</dd>
        <dt className="text-faint">Degrees</dt><dd className="font-mono text-[13px]">{sky.ra.toFixed(5)} {sky.dec >= 0 ? "+" : ""}{sky.dec.toFixed(5)}</dd>
        <dt className="text-faint">Gaia DR3</dt><dd>{near ? describeStar(near.star) : "No catalogued star within one pixel."}</dd>
      </dl>
      {!near && (
        <p className="mt-1 text-xs text-muted">A source here with no Gaia counterpart may be very red, very faint in visible light, or moving.</p>
      )}
      <div className="mt-2 flex flex-wrap gap-2">
        <a href={simbadUrl(sky.ra, sky.dec)} target="_blank" rel="noreferrer" className={buttonClass}>SIMBAD (CfA) ↗</a>
        <Copy label="Copy report" text={reportText({ ra: sky.ra, dec: sky.dec, link, band, looks, known })} />
        {canMeasure && (
          <button type="button" onClick={() => void run()} disabled={measure.state === "busy"} className={buttonClass}>
            Measure light curve and spectrum
          </button>
        )}
      </div>
      {measure.text && (
        <p role={measure.state === "error" ? "alert" : "status"} className={`mt-1 text-sm ${measure.state === "error" ? "text-danger" : "text-muted"}`}>
          {measure.text}
        </p>
      )}
    </Section>
  );
}

// ---- photometry: light curve and spectrum -------------------------------------------------------

export function PhotometrySection({ lc, detector, title, note }: { lc: Lightcurve; detector: number | null; title: string; note?: string }) {
  const det = detector != null && lc.points.some((p) => p.detector === detector && (p.flux_jy ?? 0) > 0) ? detector : busiestDetector(lc.points);
  const bins = det == null ? [] : binByVisit(lc.points.filter((p) => p.detector === det));
  const spec = spectrumPoints(lc.spectrum);
  const band = BANDS.find((b) => b.detector === det);
  const floors = bins.some((b) => b.floor) || spec.some((p) => p.lower_limit);
  return (
    <section className="mt-6 border-t border-rule pt-4" aria-label={title}>
      <h3 className="text-base font-semibold">{title}</h3>
      {note && <p className="mt-0.5 text-sm text-muted">{note}</p>}
      <div className="mt-3 grid gap-6 xl:grid-cols-2">
        <figure className="m-0">
          <figcaption className="mb-1 text-sm">
            <span className="font-semibold">Light curve</span>
            {band && <span className="text-muted"> · detector {band.detector}, {band.range[0]}–{band.range[1]} µm (follows the band selected above)</span>}
          </figcaption>
          <LightCurveChart bins={bins} label={`${title}: light curve`} />
          <p className="text-xs text-muted">
            One point per visit: the median of its frames ({bins.reduce((n, b) => n + b.n, 0)} in all), with the full range as a bar.
          </p>
        </figure>
        <figure className="m-0">
          <figcaption className="mb-1 text-sm"><span className="font-semibold">Spectrum</span><span className="text-muted"> · all dates combined</span></figcaption>
          <SpectrumChart points={spec} label={`${title}: spectrum`} />
          <p className="text-xs text-muted">Dashed lines mark where common molecules absorb or emit; hover a label for details.</p>
        </figure>
      </div>
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-accent">What the marked wavelengths mean</summary>
        <ul className="mt-2 grid gap-x-6 gap-y-1 sm:grid-cols-2">
          {FEATURES.map((f) => (
            <li key={`${f.label}${f.um}`}><span className="tabular font-semibold">{f.label}, {f.um} µm</span> <span className="text-muted">— {f.note}</span></li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-muted">
          A dip or bump at a marked wavelength suggests that molecule; it is a guide, not a fit. Each spectrum point combines
          frames from different dates, so a variable star adds scatter.
        </p>
      </details>
      {floors && (
        <p className="mt-2 text-xs text-muted">Open circles: the star saturated the detector, so its true brightness is at least the value shown.</p>
      )}
    </section>
  );
}

// ---- share -------------------------------------------------------------------------------------

export function ShareSection({ url }: { url: string }) {
  const u = new URL(url);
  u.searchParams.set("embed", "1");
  const embed = `<iframe src="${u.toString().replace(/&/g, "&amp;").replace(/"/g, "&quot;")}" title="Tombaugh" width="520" height="640" loading="lazy" style="border:0"></iframe>`;
  return (
    <Section title="Link to this view">
      <div className="flex flex-wrap gap-2">
        <Copy label="Copy link" text={url} />
        <Copy label="Copy embed code" text={embed} />
      </div>
    </Section>
  );
}

/** What the SPHEREx pipeline flagged in the frame on show (the layer is drawn over the picture). */
export function FlagSection({ entry, single }: { entry: FlagFetch | "busy" | undefined; single: boolean }) {
  const body = (() => {
    if (!single) return <p className="text-xs text-muted">Flags are per frame. Use Blink on one detector.</p>;
    if (!entry || entry === "busy") return <p className="text-xs text-muted">Reading this frame's flags…</p>;
    if (!entry.ok) return <p role="alert" className="text-xs">{entry.error}</p>;
    const pct = (f: number) => (f === 0 ? "0" : f < 0.001 ? "under 0.1" : (f * 100).toFixed(1));
    const source = entry.data.categories.find((c) => c.key === "source");
    return (
      <div className="space-y-1 text-xs text-muted" aria-label="Flag legend">
        <ul className="space-y-0.5">
          {entry.data.categories.filter((c) => c.drawn).map((c) => (
            <li key={c.key} className="flex items-center gap-1.5" title={c.bits.join(", ")}>
              <span className="inline-block h-3 w-3 shrink-0 border border-rule-strong" style={{ background: c.colour }} />
              <span className="text-ink">{c.label}</span>: {pct(c.fraction)}%
            </li>
          ))}
        </ul>
        <p>
          The pipeline's flags for this frame. Flagged pixels are still shown by value (most are fine) except detector
          defects; measurements leave them all out.
          {source && source.fraction > 0 && <> {pct(source.fraction)}% is also flagged as a known source. That is information, not bad data, so it is not drawn.</>}
        </p>
      </div>
    );
  })();
  return <Section title="Flagged pixels">{body}</Section>;
}
