import { useEffect, useRef, type ReactNode } from "react";
import { FAST_ARCSEC_YR, motionArcsecYr } from "../lib/gaia";
import { starAt, type OverlayData } from "../lib/overlay";

/** Overlay colours, chosen to stay distinct on grey and on false-colour pictures. Each mark is
 *  drawn twice: a dark halo underneath, then the colour, so it reads on any background. */
export const MARK = {
  gaia: "#5fd3ff",
  fast: "#ffd23f",
  asteroid: "#ff5bd1",
  pick: "#ff4b3e",
};

interface Props {
  rgba: Uint8ClampedArray | null;
  width: number;
  height: number;
  fallbackSrc?: string;
  alt: string;
  stars?: { data: OverlayData; t: number; maxMag: number; onlyFast?: boolean } | null;
  asteroids?: { u: number; v: number; name: string; vmag: number | null }[] | null;
  /** The picked pixel, as the fraction of the picture at its centre. */
  picked?: { u: number; v: number } | null;
  onPick?(p: { u: number; v: number }): void;
  children?: ReactNode;
}

function Haloed({ children, colour, width = 1.5 }: { children: (stroke: string, w: number) => ReactNode; colour: string; width?: number }) {
  return (
    <>
      {children("rgba(0,0,0,0.85)", width + 2)}
      {children(colour, width)}
    </>
  );
}

export function SkyView({ rgba, width, height, fallbackSrc, alt, stars, asteroids, picked, onPick, children }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = canvas.current;
    if (!c || !rgba || rgba.length !== width * height * 4) return;
    c.width = width;
    c.height = height;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.putImageData(new ImageData(new Uint8ClampedArray(rgba), width, height), 0, 0);
  }, [rgba, width, height]);

  function click(e: React.MouseEvent<HTMLDivElement>) {
    if (!onPick) return;
    const r = e.currentTarget.getBoundingClientRect();
    onPick({ u: (e.clientX - r.left) / r.width, v: (e.clientY - r.top) / r.height });
  }

  const px = width > 0 ? 1 / width : 0.02; // one picture pixel, as a fraction
  const ring = (mag: number | null) => Math.max(1.1 * px, Math.min(1.9 * px, (mag == null ? 1.4 : 2.6 - mag / 10) * px));
  const shownStars = stars?.data.stars.filter((s) =>
    (motionArcsecYr(s) ?? 0) >= FAST_ARCSEC_YR || (!stars.onlyFast && (s.g_mag == null || s.g_mag <= stars.maxMag))) ?? [];

  return (
    <div className="relative aspect-square w-full overflow-hidden bg-sky">
      <div className={`absolute inset-0 ${onPick ? "cursor-crosshair" : ""}`} onClick={click}>
        {rgba ? (
          <canvas ref={canvas} role="img" aria-label={alt} className="pixelated h-full w-full" />
        ) : fallbackSrc ? (
          <img src={fallbackSrc} alt={alt} className="pixelated h-full w-full" />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-white/60">Loading…</div>
        )}
        <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden>
          {stars && shownStars.map((s, i) => {
            const p = starAt(s, stars.data, stars.t);
            const fast = (motionArcsecYr(s) ?? 0) >= FAST_ARCSEC_YR;
            return (
              <Haloed key={s.source_id ?? i} colour={fast ? MARK.fast : MARK.gaia} width={fast ? 1.75 : 1.25}>
                {(stroke, w) => (
                  <circle cx={p.u} cy={p.v} r={fast ? 2.4 * px : ring(s.g_mag)} fill="none" stroke={stroke} strokeWidth={w}
                    vectorEffect="non-scaling-stroke" />
                )}
              </Haloed>
            );
          })}
          {asteroids?.map((a, i) => {
            const d = 2.2 * px;
            return (
              <Haloed key={`${a.name}${i}`} colour={MARK.asteroid}>
                {(stroke, w) => (
                  <path d={`M${a.u} ${a.v - d}L${a.u + d} ${a.v}L${a.u} ${a.v + d}L${a.u - d} ${a.v}Z`} fill="none"
                    stroke={stroke} strokeWidth={w} vectorEffect="non-scaling-stroke" />
                )}
              </Haloed>
            );
          })}
          {picked && (
            <Haloed colour={MARK.pick} width={1.25}>
              {(stroke, w) => (
                <g stroke={stroke} strokeWidth={w} fill="none">
                  <rect x={picked.u - px / 2} y={picked.v - px / 2} width={px} height={px} vectorEffect="non-scaling-stroke" />
                  <path vectorEffect="non-scaling-stroke"
                    d={`M${picked.u - 2.5 * px} ${picked.v}H${picked.u - 1.2 * px}M${picked.u + 1.2 * px} ${picked.v}H${picked.u + 2.5 * px}` +
                      `M${picked.u} ${picked.v - 2.5 * px}V${picked.v - 1.2 * px}M${picked.u} ${picked.v + 1.2 * px}V${picked.v + 2.5 * px}`} />
                </g>
              )}
            </Haloed>
          )}
        </svg>
      </div>
      {children}
    </div>
  );
}
