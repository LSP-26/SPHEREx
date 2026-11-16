import { useEffect, useState } from "react";
import { EXAMPLES, type Example } from "../lib/examples";
import { parseEvent } from "../lib/events";
import { defaultBand, looksByBand } from "../lib/looks";
import type { Slot } from "../lib/reducer";
import { SHOWCASE_BASE, type Showcase } from "../lib/showcases";
import { MovingStar } from "./MovingStar";
import { SearchBar, type Go } from "./SearchBar";
import { ThemeToggle } from "./ThemeToggle";

/** A small thumbnail that steps through an example's looks in its default band. */
function Thumb({ ex, showcase }: { ex: Example; showcase?: Showcase }) {
  const [frames, setFrames] = useState<string[]>([]);
  const [i, setI] = useState(0);
  useEffect(() => {
    if (!showcase) return;
    let alive = true;
    const base = new URL(SHOWCASE_BASE + showcase.events, window.location.href);
    void fetch(base).then((r) => (r.ok ? r.json() : null)).then((body: { events?: unknown[] } | null) => {
      if (!alive || !body?.events) return;
      const slots: Slot[] = body.events.map(parseEvent).flatMap((e) => (e?.type === "frame" ? [e] : []));
      const byBand = looksByBand(slots);
      const band = ex.band && byBand.has(ex.band) ? ex.band : defaultBand(byBand);
      setFrames((band != null ? byBand.get(band) ?? [] : []).map((l) => new URL(l.frame.png_url, base).href));
    }).catch(() => {});
    return () => { alive = false; };
  }, [showcase, ex.band]);
  useEffect(() => {
    if (frames.length < 2) return;
    const id = setInterval(() => setI((x) => (x + 1) % frames.length), 1000);
    return () => clearInterval(id);
  }, [frames.length]);
  return (
    <div className="h-16 w-16 shrink-0 bg-sky">
      {frames.length > 0 && <img src={frames[i % frames.length]} alt="" className="pixelated h-full w-full" />}
    </div>
  );
}

export function Home({ showcases, onGo, problem }: { showcases: Showcase[]; onGo(go: Go): void; problem?: string | null }) {
  return (
    <main className="mx-auto max-w-6xl px-4 pb-16">
      <header className="flex items-start justify-between gap-4 border-b border-rule pb-5 pt-8">
        <div>
          <h1 className="wordmark text-2xl">Tombaugh</h1>
          <p className="mt-1 text-muted">Blink through repeat SPHEREx images of any patch of sky</p>
        </div>
        <ThemeToggle />
      </header>

      <section className="mt-6" aria-label="Search">
        <SearchBar large showcases={showcases} onGo={onGo} />
        {problem && <p role="alert" className="mt-2 text-sm text-danger">This link has a problem: {problem}</p>}
      </section>

      <div className="mt-8 grid items-stretch gap-8 lg:grid-cols-[minmax(18rem,28rem)_minmax(0,1fr)]">
        <MovingStar showcases={showcases} />

        <section className="flex min-w-0 flex-col" aria-label="Test targets">
          <h2 className="text-lg font-semibold leading-none">Test targets</h2>
          <ul className="mt-3 flex flex-1 flex-col divide-y divide-rule border-y border-rule">
            {EXAMPLES.map((ex) => (
              <li key={ex.key} className="flex flex-1">
                <button type="button" onClick={() => onGo({ kind: "example", example: ex })}
                  className="flex w-full items-center gap-4 py-2 text-left hover:bg-accent-soft">
                  <Thumb ex={ex} showcase={showcases.find((s) => s.key === ex.key)} />
                  <span className="min-w-0">
                    <span className="block font-semibold text-accent">{ex.name}</span>
                    <span className="block text-sm text-muted">{ex.kind}. {ex.hook}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <div className="mt-16 grid gap-10 border-t border-rule pt-8 leading-relaxed md:grid-cols-3 md:gap-8">
        <section className="space-y-3" aria-label="Method">
          <h2 className="text-lg font-semibold">Method</h2>
          <ol className="list-decimal space-y-2 pl-5 text-sm">
            <li>
              <span className="font-semibold">Frames.</span> A position falls at nearly the same wavelength on a given detector
              every pass, so frames are compared within one detector only. Differences then come from the sky, not the instrument.
            </li>
            <li>
              <span className="font-semibold">Display.</span> The server cleans flagged pixels and resamples onto a north-up grid
              (6.15″ pixels). Blink plays the frames in order. Difference subtracts the most distant epoch after normalising each
              frame by its sky level and noise, so static sources cancel.
            </li>
            <li>
              <span className="font-semibold">Identification.</span> Gaia DR3 stars are drawn at each frame's epoch using their
              proper motions, and known asteroids come from IMCCE SkyBoT. Click a pixel to look it up in SIMBAD or, with the
              server running, measure it in every frame for a light curve and spectrum.
            </li>
          </ol>
        </section>

        <section className="space-y-3 text-sm text-muted" aria-label="Limitations">
          <h2 className="text-lg font-semibold text-ink">Data and limitations</h2>
          <p>
            Single frames are shallower than the final survey. Pixels are 6.15″, so motions below about 3″ per year are hard to
            see by eye over the current baseline. Bright stars saturate. The colour view is false colour and not flux-calibrated.
          </p>
        </section>

        <section className="space-y-3" aria-label="About">
          <h2 className="text-lg font-semibold">About</h2>
          <p className="text-sm">
            SPHEREx maps the whole sky every six months at 0.75 to 5 µm. Pick a position and Tombaugh shows every frame of it
            in time order, one detector at a time, so anything that moves or varies stands out.
          </p>
          <p className="text-sm text-muted">
            Named after Clyde Tombaugh, who found Pluto in 1930 by blinking photographic plates of the same field.
          </p>
        </section>
      </div>

    </main>
  );
}
