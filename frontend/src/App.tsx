import { useCallback, useEffect, useState } from "react";
import { Explorer, type Target } from "./components/Explorer";
import { Footer } from "./components/Footer";
import { Home } from "./components/Home";
import { SearchBar, type Go } from "./components/SearchBar";
import { ThemeToggle } from "./components/ThemeToggle";
import { useFlipbook } from "./hooks/useFlipbook";
import { useShowcases } from "./hooks/useShowcases";
import { EXAMPLES } from "./lib/examples";
import { readPage, writePage, type BandChoice, type Mode, type PageState } from "./lib/page";
import { eventsSrc, flagsSrc, overlaySrc, showcaseFor } from "./lib/showcases";
import { toQuery, withDefaults } from "./lib/urlState";

const read = () => readPage(window.location.search);

export default function App() {
  const { state, start, startStatic } = useFlipbook();
  const showcases = useShowcases();
  const [page, setPage] = useState<PageState>(read);

  // the URL is the state: load whatever it points at, and again on back/forward
  const load = useCallback((pg: PageState) => {
    const s = pg.source;
    if (s?.kind === "live") start(toQuery(s.params));
    else if (s?.kind === "static") startStatic(s.url);
  }, [start, startStatic]);

  useEffect(() => {
    load(page);
    const onPop = () => { const pg = read(); setPage(pg); load(pg); };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
    // only on first load; later changes go through navigate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // an example plays once the manifest says where its recording is
  const exampleKey = page.source?.kind === "example" ? page.source.key : null;
  const showcase = exampleKey ? showcases.find((s) => s.key === exampleKey) : page.source?.kind === "static" ? showcaseFor(page.source.url, showcases) : undefined;
  useEffect(() => {
    if (exampleKey && showcase) startStatic(eventsSrc(showcase));
  }, [exampleKey, showcase, startStatic]);

  function navigate(next: PageState, push = true) {
    const q = writePage(next, window.location.search);
    window.history[push ? "pushState" : "replaceState"](null, "", `${window.location.pathname}${q ? `?${q}` : ""}`);
    setPage(next);
  }

  function go(g: Go) {
    const base = { band: null, mode: "blink" as Mode, embed: false, errors: [] };
    if (g.kind === "example") {
      navigate({ ...base, source: { kind: "example", key: g.example.key }, name: null });
    } else {
      const params = withDefaults({ ra: g.ra, dec: g.dec, fov_arcmin: g.fov });
      navigate({ ...base, source: { kind: "live", params }, name: g.name });
      start(toQuery(params));
      setNote(g.note ?? null);
    }
    window.scrollTo({ top: 0 });
  }
  const [note, setNote] = useState<string | null>(null);

  const setView = (band: BandChoice | null, mode: Mode) => navigate({ ...page, band, mode }, false);

  const example = exampleKey ? EXAMPLES.find((e) => e.key === exampleKey) : showcase ? EXAMPLES.find((e) => e.key === showcase.key) : undefined;
  const live = page.source?.kind === "live" ? page.source.params : null;
  const target: Target | null = live
    ? { ra: live.ra, dec: live.dec, fov: live.fov_arcmin, name: page.name, note: note ?? undefined }
    : showcase
      ? { ra: showcase.ra, dec: showcase.dec, fov: showcase.fov_arcmin, name: example?.name ?? showcase.name }
      : null;

  const origin = `${window.location.origin}${window.location.pathname}`;
  const shareUrl = `${origin}?${writePage({ ...page, embed: false }, window.location.search)}`;
  const unknownExample = exampleKey && showcases.length > 0 && !showcase;

  if (!target || unknownExample) {
    if (exampleKey && !unknownExample) return <div className="min-h-screen" />; // the manifest is still loading
    const problem = unknownExample ? `there is no example called "${exampleKey}"` : page.errors.length ? page.errors.join("; ") : null;
    return (
      <>
        <Home showcases={showcases} onGo={go} problem={problem} />
        <Footer />
      </>
    );
  }

  const explorer = (
    <Explorer
      state={state}
      target={target}
      example={example}
      overlayUrl={showcase ? overlaySrc(showcase) : undefined}
      flagsUrl={showcase ? flagsSrc(showcase) : undefined}
      live={!!live}
      band={page.band}
      mode={page.mode}
      embed={page.embed}
      shareUrl={shareUrl}
      fullPageUrl={shareUrl}
      onView={setView}
      onRetry={live ? () => start(toQuery(live)) : undefined}
    />
  );
  if (page.embed) return explorer;

  return (
    <>
      <div className="mx-auto max-w-7xl px-4 pb-12">
        <header className="mb-5 flex flex-wrap items-start gap-x-8 gap-y-3 border-b border-rule pb-3 pt-4">
          <a href={window.location.pathname} className="wordmark mt-[1.15rem] text-lg leading-9" aria-label="Tombaugh, home">Tombaugh</a>
          <div className="min-w-0 flex-1 basis-80">
            <SearchBar key={`${target.ra},${target.dec},${target.fov},${target.name ?? ""}`} showcases={showcases} onGo={go}
              current={{ ra: target.ra, dec: target.dec, fov: target.fov, name: target.name, example: live ? undefined : example }} />
          </div>
          <div className="mt-5 lg:mt-6"><ThemeToggle /></div>
        </header>
        <main>{explorer}</main>
        {example && live === null && (
          <p className="mt-6 text-xs text-muted">
            This example is recorded, so it plays without a server.{" "}
            <a className="underline"
              href={`?${writePage({ source: { kind: "live", params: withDefaults({ ra: target.ra, dec: target.dec, fov_arcmin: target.fov }) }, name: target.name, band: null, mode: "blink", embed: false }, window.location.search)}`}>
              Load it fresh from the archive
            </a>{" "}
            (needs the live server).
          </p>
        )}
      </div>
      <Footer />
    </>
  );
}
