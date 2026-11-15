import { useEffect, useState } from "react";
import { parseEvent } from "../lib/events";
import { defaultBand, looksByBand } from "../lib/looks";
import type { Slot } from "../lib/reducer";
import { starAt, type OverlayData, type OverlayStar } from "../lib/overlay";
import { SHOWCASE_BASE, type Showcase } from "../lib/showcases";

interface Shot {
  url: string;
  date: string;
  um: number;
  mjd: number;
}

const mjdToDate = (mjd: number) => new Date((mjd - 40587) * 86_400_000).toISOString().slice(0, 10);

/** The real recorded frames of Barnard's Star (band 2), oldest first. Nothing is invented. */
function useShots(showcase?: Showcase): Shot[] {
  const [shots, setShots] = useState<Shot[]>([]);
  useEffect(() => {
    if (!showcase) return;
    let alive = true;
    const base = new URL(SHOWCASE_BASE + showcase.events, window.location.href);
    void fetch(base)
      .then((r) => (r.ok ? r.json() : null))
      .then((body: { events?: unknown[] } | null) => {
        if (!alive || !body?.events) return;
        const slots: Slot[] = body.events.map(parseEvent).flatMap((e) => (e?.type === "frame" ? [e] : []));
        const byBand = looksByBand(slots);
        const band = byBand.has(2) ? 2 : defaultBand(byBand);
        const looks = band != null ? byBand.get(band) ?? [] : [];
        const list = looks.map((l) => ({
          url: new URL(l.frame.png_url, base).href,
          date: mjdToDate(l.t),
          um: l.lambda,
          mjd: l.t,
        }));
        list.forEach((s) => { new Image().src = s.url; });
        setShots(list);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [showcase]);
  return shots;
}

/** Gaia's saved stars for the field, and the fastest one (Barnard's Star), so a ring can follow it. */
function useMover(showcase?: Showcase): { data: OverlayData; star: OverlayStar } | null {
  const [m, setM] = useState<{ data: OverlayData; star: OverlayStar } | null>(null);
  useEffect(() => {
    if (!showcase) return;
    let alive = true;
    void fetch(new URL(`${SHOWCASE_BASE}${showcase.key}/overlay.json`, window.location.href))
      .then((r) => (r.ok ? r.json() : null))
      .then((data: OverlayData | null) => {
        if (!alive || !data?.stars?.length) return;
        const speed = (s: OverlayStar) => Math.hypot(s.vx, s.vy);
        const star = data.stars.reduce((a, b) => (speed(b) > speed(a) ? b : a));
        setM({ data, star });
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [showcase]);
  return m;
}

const reduced = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** A real blink of Barnard's Star that plays by itself, with a ring on where Gaia says the star is at each date. */
export function MovingStar({ showcases }: { showcases: Showcase[] }) {
  const barnard = showcases.find((s) => s.key === "barnard");
  const shots = useShots(barnard);
  const mover = useMover(barnard);
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(() => !reduced());
  useEffect(() => {
    if (!playing || shots.length < 2) return;
    const id = setInterval(() => setI((x) => (x + 1) % shots.length), 900);
    return () => clearInterval(id);
  }, [playing, shots.length]);
  const shot = shots[i % Math.max(shots.length, 1)];

  return (
    <figure className="w-full" aria-label="Moving star example">
      <button
        type="button"
        onClick={() => setPlaying((p) => !p)}
        aria-label={playing ? "Pause the blink" : "Play the blink"}
        className="relative block aspect-square w-full overflow-hidden border border-rule bg-sky"
      >
        {shot && <img src={shot.url} alt="Barnard's Star in SPHEREx frames taken on different dates" className="pixelated h-full w-full" />}
        {!shot && <span className="absolute inset-0 grid place-items-center text-sm text-white/50">Loading frames…</span>}
        {shot && mover && <Ring mover={mover} t={shot.mjd} />}
        {shot && <span className="absolute left-2 top-2 rounded-sm bg-black/60 px-2 py-0.5 text-xs tabular text-white/90">{shot.date}</span>}
        {shot && <span className="absolute right-2 top-2 rounded-sm bg-black/60 px-2 py-0.5 text-xs tabular text-white/90">{shot.um.toFixed(2)} µm</span>}
      </button>
    </figure>
  );
}

/** A ring that follows the fastest Gaia star: where its catalogue proper motion puts it at the frame's date. */
function Ring({ mover, t }: { mover: { data: OverlayData; star: OverlayStar }; t: number }) {
  const p = starAt(mover.star, mover.data, t);
  return (
    <svg viewBox="0 0 1 1" className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true">
      <circle cx={p.u} cy={p.v} r={0.09} fill="none" stroke="#ffd27a" strokeWidth={2} vectorEffect="non-scaling-stroke"
        style={{ transition: "cx 0.6s ease, cy 0.6s ease" }} />
    </svg>
  );
}
