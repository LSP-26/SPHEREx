import { linearScale, logScale } from "../lib/lightcurve";
import { dayMonthYear, monthYear } from "../lib/looks";
import { FEATURES, type VisitBin } from "../lib/photometry";
import type { SpectrumPoint } from "../lib/lightcurve";

const W = 720;
const H = 250;
const M = { l: 64, r: 16, t: 26, b: 42 };
const PW = W - M.l - M.r;
const PH = H - M.t - M.b;
const SERIES = "var(--color-accent)";
const AXIS = "var(--color-faint)";
const GRID = "var(--color-grid)";

export function fmtFlux(v: number): string {
  if (v >= 1) return `${Number(v.toPrecision(2))}`;
  if (v >= 1e-3) return `${Number((v * 1e3).toPrecision(2))} m`;
  return `${Number((v * 1e6).toPrecision(2))} µ`;
}

function fluxText(v: number): string {
  if (v >= 1) return `${v.toPrecision(3)} Jy`;
  if (v >= 1e-3) return `${(v * 1e3).toPrecision(3)} mJy`;
  return `${(v * 1e6).toPrecision(3)} µJy`;
}

function Frame({ label, xTitle, yTicks, xTicks, children }: {
  label: string; xTitle: string; yTicks: { y: number; text: string }[]; xTicks: { x: number; text: string }[];
  children: React.ReactNode;
}) {
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="w-full">
      <g transform={`translate(${M.l},${M.t})`}>
        {yTicks.map((t) => (
          <g key={`y${t.text}${t.y}`}>
            <line x1={0} x2={PW} y1={t.y} y2={t.y} stroke={GRID} />
            <text x={-8} y={t.y} dy="0.32em" textAnchor="end" fontSize={11} fill={AXIS}>{t.text}</text>
          </g>
        ))}
        <line x1={0} x2={PW} y1={PH} y2={PH} stroke="var(--color-rule-strong)" />
        {xTicks.map((t) => (
          <g key={`x${t.text}${t.x}`}>
            <line x1={t.x} x2={t.x} y1={PH} y2={PH + 4} stroke="var(--color-rule-strong)" />
            <text x={t.x} y={PH + 17} textAnchor="middle" fontSize={11} fill={AXIS}>{t.text}</text>
          </g>
        ))}
        {children}
      </g>
      <text x={M.l + PW / 2} y={H - 6} textAnchor="middle" fontSize={11} fill="var(--color-muted)">{xTitle}</text>
      <text transform={`translate(13 ${M.t + PH / 2}) rotate(-90)`} textAnchor="middle" fontSize={11} fill="var(--color-muted)">
        Flux density (Jy)
      </text>
    </svg>
  );
}

function thin<T>(xs: T[], max = 7): T[] {
  if (xs.length <= max) return xs;
  const step = Math.ceil(xs.length / max);
  return xs.filter((_, i) => i % step === 0);
}

/** Brightness over time for one detector: one point per visit (median), its full range as a bar. */
export function LightCurveChart({ bins, label }: { bins: VisitBin[]; label: string }) {
  if (bins.length === 0) return <p className="text-sm text-muted">No measured frames in this band.</p>;
  const ts = bins.map((b) => b.t);
  const pad = Math.max(10, (Math.max(...ts) - Math.min(...ts)) * 0.03);
  const x = linearScale(Math.min(...ts) - pad, Math.max(...ts) + pad, PW);
  const y = logScale(Math.min(...bins.map((b) => b.lo)), Math.max(...bins.map((b) => b.hi)), PH);
  const inX = x.ticks.filter((v) => v >= Math.min(...ts) - pad && v <= Math.max(...ts) + pad);
  const path = bins.map((b, i) => `${i ? "L" : "M"}${x.to(b.t).toFixed(1)} ${y.to(b.flux).toFixed(1)}`).join(" ");
  return (
    <Frame label={label} xTitle="Date"
      yTicks={thin(y.ticks, 6).map((v) => ({ y: y.to(v), text: fmtFlux(v) }))}
      xTicks={thin(inX, 6).map((v) => ({ x: x.to(v), text: monthYear(v) }))}>
      <path d={path} fill="none" stroke={SERIES} strokeWidth={1.25} strokeOpacity={0.45} />
      {bins.map((b, i) => {
        const cx = x.to(b.t);
        return (
          <g key={i}>
            {b.hi > b.lo && <line x1={cx} x2={cx} y1={y.to(b.hi)} y2={y.to(b.lo)} stroke={SERIES} strokeOpacity={0.5} />}
            <circle cx={cx} cy={y.to(b.flux)} r={3.5} fill={b.floor ? "var(--color-panel)" : SERIES} stroke={SERIES} strokeWidth={1.5} />
            <circle cx={cx} cy={y.to(b.flux)} r={9} fill="transparent">
              <title>{`${dayMonthYear(b.t)} · ${b.lambda.toFixed(2)} µm · ${b.floor ? "at least " : ""}${fluxText(b.flux)} (median of ${b.n} frame${b.n === 1 ? "" : "s"}${b.hi > b.lo ? `, range ${fluxText(b.lo)} to ${fluxText(b.hi)}` : ""})`}</title>
            </circle>
          </g>
        );
      })}
    </Frame>
  );
}

/** Flux against wavelength, joined, with the bands of common molecules marked. */
export function SpectrumChart({ points, label }: { points: (SpectrumPoint & { flux_jy: number })[]; label: string }) {
  if (points.length < 2) return <p className="text-sm text-muted">Not enough wavelengths measured for a spectrum.</p>;
  const x = linearScale(0.7, 5.05, PW);
  const lows = points.map((p) => Math.max(p.flux_jy - (p.scatter_jy ?? 0), p.flux_jy * 0.5));
  const highs = points.map((p) => p.flux_jy + (p.scatter_jy ?? 0));
  const y = logScale(Math.min(...lows), Math.max(...highs), PH);
  const xt = [1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5];
  // a gap between detectors is not joined across (no data there)
  let path = "";
  points.forEach((p, i) => {
    const jump = i > 0 && p.lambda_um - points[i - 1].lambda_um > 0.25;
    path += `${i === 0 || jump ? "M" : "L"}${x.to(p.lambda_um).toFixed(1)} ${y.to(p.flux_jy).toFixed(1)} `;
  });
  return (
    <Frame label={label} xTitle="Wavelength (µm)"
      yTicks={thin(y.ticks, 6).map((v) => ({ y: y.to(v), text: fmtFlux(v) }))}
      xTicks={xt.map((v) => ({ x: x.to(v), text: String(v) }))}>
      {FEATURES.map((f, i) => (
        <g key={`${f.label}${f.um}`}>
          <line x1={x.to(f.um)} x2={x.to(f.um)} y1={4} y2={PH} stroke="var(--color-rule-strong)" strokeDasharray="3 3" />
          <text x={x.to(f.um)} y={i % 2 ? -14 : -2} textAnchor="middle" fontSize={10.5} fill="var(--color-muted)">
            {f.label}
            <title>{`${f.um} µm · ${f.note}`}</title>
          </text>
        </g>
      ))}
      <path d={path} fill="none" stroke={SERIES} strokeWidth={1.5} />
      {points.map((p, i) => {
        const cx = x.to(p.lambda_um);
        return (
          <g key={i}>
            {p.scatter_jy != null && p.scatter_jy > 0 && (
              <line x1={cx} x2={cx} y1={y.to(p.flux_jy + p.scatter_jy)} y2={y.to(Math.max(p.flux_jy - p.scatter_jy, p.flux_jy * 0.5))}
                stroke={SERIES} strokeOpacity={0.45} />
            )}
            <circle cx={cx} cy={y.to(p.flux_jy)} r={2.5} fill={p.lower_limit ? "var(--color-panel)" : SERIES} stroke={SERIES} strokeWidth={1.25} />
            <circle cx={cx} cy={y.to(p.flux_jy)} r={7} fill="transparent">
              <title>{`${p.lambda_um.toFixed(2)} µm · ${p.lower_limit ? "at least " : ""}${fluxText(p.flux_jy)} · ${p.n} frame${p.n === 1 ? "" : "s"}`}</title>
            </circle>
          </g>
        );
      })}
    </Frame>
  );
}
