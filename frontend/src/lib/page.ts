// The page URL is the whole view, so any view can be shared or embedded:
//   ?ex=barnard                     a recorded example (plays with no server)
//   ?ra=269.45&dec=4.77&fov=10      any patch of sky, from the live server (the API query, see urlState)
//   &name=Barnard%27s%20Star        the label shown for it
//   &band=2 | &band=colour          which detector band, or the colour view
//   &mode=diff                      difference view instead of the blink
//   &embed=1                        just the viewer, for an <iframe>
// ?static=<events.json> still replays any recorded events file.
import { fromQuery, toQuery, type FlipbookParams } from "./urlState";

export type Mode = "blink" | "diff";
export type BandChoice = number | "colour";

export type Source =
  | { kind: "example"; key: string }
  | { kind: "static"; url: string }
  | { kind: "live"; params: FlipbookParams };

export interface PageState {
  source: Source | null;
  name: string | null;
  band: BandChoice | null;
  mode: Mode;
  embed: boolean;
  errors: string[];
}

const VIEW_KEYS = ["ex", "static", "name", "band", "mode", "embed", "api"];

export function readPage(search: string): PageState {
  const q = new URLSearchParams(search);
  const errors: string[] = [];
  const bandRaw = q.get("band");
  let band: BandChoice | null = null;
  if (bandRaw === "colour" || bandRaw === "color") band = "colour";
  else if (bandRaw !== null) {
    const n = Number(bandRaw);
    if (Number.isInteger(n) && n >= 1 && n <= 6) band = n;
    else errors.push("band must be 1 to 6 or colour");
  }
  const mode: Mode = q.get("mode") === "diff" ? "diff" : "blink";
  const embed = ["1", "true"].includes(q.get("embed") ?? "");
  const name = q.get("name");

  let source: Source | null = null;
  const ex = q.get("ex");
  const stat = q.get("static");
  if (ex) source = { kind: "example", key: ex };
  else if (stat) source = { kind: "static", url: stat };
  else if (q.has("ra") || q.has("dec")) {
    const rest = new URLSearchParams(search);
    for (const k of VIEW_KEYS) rest.delete(k);
    const parsed = fromQuery(rest.toString());
    if (parsed.ok) source = { kind: "live", params: parsed.params };
    else errors.push(...parsed.errors);
  }
  return { source, name, band, mode, embed, errors };
}

/** The query string for a view. Keeps `api` from the current URL so a shared link stays on the same server. */
export function writePage(p: Omit<PageState, "errors">, keep: string = ""): string {
  const q = new URLSearchParams();
  const s = p.source;
  if (s?.kind === "example") q.set("ex", s.key);
  else if (s?.kind === "static") q.set("static", s.url);
  else if (s?.kind === "live") for (const [k, v] of new URLSearchParams(toQuery(s.params))) q.set(k, v);
  if (p.name && s?.kind !== "example") q.set("name", p.name);
  if (p.band !== null) q.set("band", String(p.band));
  if (p.mode === "diff") q.set("mode", "diff");
  if (p.embed) q.set("embed", "1");
  const api = new URLSearchParams(keep).get("api");
  if (api) q.set("api", api);
  return q.toString();
}
