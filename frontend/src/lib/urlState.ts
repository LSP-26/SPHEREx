// The page URL is the state, and it is also the API query string. This mirrors
// contracts/url_state.py exactly (same short names, same number formatting, defaults omitted),
// and urlState.test.ts checks it against strings produced by the Python code.

export type ViewMode = "sky" | "mover" | "spectral";
export type Orient = "north" | "detector";

export interface FlipbookParams {
  ra: number;
  dec: number;
  fov_arcmin: number;
  view: ViewMode;
  detector: number | null;
  lambda_min_um: number | null;
  lambda_max_um: number | null;
  preview_cap: number;
  use_zodi: boolean;
  release: string | null;
  orient: Orient;
  fill: boolean;
}

export const DEFAULTS = {
  fov_arcmin: 5,
  view: "sky",
  detector: null,
  lambda_min_um: null,
  lambda_max_um: null,
  preview_cap: 36,
  use_zodi: true,
  release: null,
  orient: "north",
  fill: true,
} as const satisfies Omit<FlipbookParams, "ra" | "dec">;

export function withDefaults(p: Pick<FlipbookParams, "ra" | "dec"> & Partial<FlipbookParams>) {
  return { ...DEFAULTS, ...p } as FlipbookParams;
}

/** Python: f"{v:.6f}".rstrip("0").rstrip(".") */
function num(v: number): string {
  return v.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
}

export function toQuery(p: FlipbookParams): string {
  const q = new URLSearchParams();
  q.set("ra", num(p.ra));
  q.set("dec", num(p.dec));
  if (p.fov_arcmin !== DEFAULTS.fov_arcmin) q.set("fov", num(p.fov_arcmin));
  if (p.view !== DEFAULTS.view) q.set("view", p.view);
  if (p.detector !== null) q.set("det", String(p.detector));
  if (p.lambda_min_um !== null) q.set("lmin", num(p.lambda_min_um));
  if (p.lambda_max_um !== null) q.set("lmax", num(p.lambda_max_um));
  if (p.preview_cap !== DEFAULTS.preview_cap) q.set("cap", String(p.preview_cap));
  if (p.use_zodi !== DEFAULTS.use_zodi) q.set("zodi", p.use_zodi ? "1" : "0");
  if (p.release !== null) q.set("release", p.release);
  if (p.orient !== DEFAULTS.orient) q.set("orient", p.orient);
  if (p.fill !== DEFAULTS.fill) q.set("fill", p.fill ? "1" : "0");
  return q.toString();
}

export type ParsedQuery = { ok: true; params: FlipbookParams } | { ok: false; errors: string[] };

function number(raw: string | null, name: string, errors: string[]): number | null {
  if (raw === null) return null;
  const v = Number(raw);
  if (raw.trim() === "" || !Number.isFinite(v)) {
    errors.push(`${name} must be a number`);
    return null;
  }
  return v;
}

function integer(raw: string | null, name: string, lo: number, hi: number, errors: string[]) {
  const v = number(raw, name, errors);
  if (v === null) return null;
  if (!Number.isInteger(v) || v < lo || v > hi) {
    errors.push(`${name} must be a whole number from ${lo} to ${hi}`);
    return null;
  }
  return v;
}

/** Reads a page URL's query string (with or without the leading "?"). Unknown keys are ignored. */
export function fromQuery(search: string): ParsedQuery {
  const q = new URLSearchParams(search);
  const errors: string[] = [];
  const ra = number(q.get("ra"), "ra", errors);
  const dec = number(q.get("dec"), "dec", errors);
  if (q.get("ra") === null) errors.push("ra is required");
  if (q.get("dec") === null) errors.push("dec is required");
  if (ra !== null && !(ra >= 0 && ra < 360)) errors.push("ra must be from 0 up to 360");
  if (dec !== null && !(dec >= -90 && dec <= 90)) errors.push("dec must be from -90 to 90");

  const fov = number(q.get("fov"), "fov", errors);
  if (fov !== null && !(fov > 0 && fov <= 120)) errors.push("fov must be above 0 and at most 120");
  const view = q.get("view") ?? DEFAULTS.view;
  if (view !== "sky" && view !== "mover" && view !== "spectral") errors.push("view must be sky, mover or spectral");
  const orient = q.get("orient") ?? DEFAULTS.orient;
  if (orient !== "north" && orient !== "detector") errors.push("orient must be north or detector");
  const det = integer(q.get("det"), "det", 1, 6, errors);
  const lmin = number(q.get("lmin"), "lmin", errors);
  const lmax = number(q.get("lmax"), "lmax", errors);
  for (const [name, v] of [["lmin", lmin], ["lmax", lmax]] as const) {
    if (v !== null && !(v > 0)) errors.push(`${name} must be above 0`);
  }
  const cap = integer(q.get("cap"), "cap", 1, 200, errors);
  const zodiRaw = q.get("zodi");
  let zodi: boolean = DEFAULTS.use_zodi;
  if (zodiRaw !== null) {
    if (["1", "true", "True"].includes(zodiRaw)) zodi = true;
    else if (["0", "false", "False"].includes(zodiRaw)) zodi = false;
    else errors.push("zodi must be 0 or 1");
  }
  const fillRaw = q.get("fill");
  let fill: boolean = DEFAULTS.fill;
  if (fillRaw !== null) {
    if (["1", "true", "True"].includes(fillRaw)) fill = true;
    else if (["0", "false", "False"].includes(fillRaw)) fill = false;
    else errors.push("fill must be 0 or 1");
  }
  if (errors.length > 0 || ra === null || dec === null) return { ok: false, errors };
  return {
    ok: true,
    params: {
      ra,
      dec,
      fov_arcmin: fov ?? DEFAULTS.fov_arcmin,
      view: view as ViewMode,
      detector: det,
      lambda_min_um: lmin,
      lambda_max_um: lmax,
      preview_cap: cap ?? DEFAULTS.preview_cap,
      use_zodi: zodi,
      release: q.get("release"),
      orient: orient as Orient,
      fill,
    },
  };
}
