// Between the picture and the sky. Every picture is north up, east left, centred on the field's
// RA and Dec, with SPHEREx's 6.15 arcsec pixels (backend/pipeline/grid.py). Positions on the
// picture are fractions of its width and height: u = 0 is the left edge, v = 0 the top.

export const PIXEL_ARCSEC = 6.15;

/** Side of the picture in pixels for a field of view (same rule as the server). */
export function pictureSizePx(fovArcmin: number): number {
  return Math.max(11, Math.ceil((fovArcmin * 60) / PIXEL_ARCSEC)) | 1;
}

export interface Field {
  ra: number;
  dec: number;
  /** Picture side in pixels. */
  size: number;
}

const RAD = Math.PI / 180;

export function pictureToSky(u: number, v: number, f: Field): { ra: number; dec: number } {
  const width = f.size * PIXEL_ARCSEC;
  const east = (0.5 - u) * width;
  const north = (0.5 - v) * width;
  const dec = f.dec + north / 3600;
  let ra = f.ra + east / 3600 / Math.cos(f.dec * RAD);
  ra = ((ra % 360) + 360) % 360;
  return { ra, dec };
}

export function skyToPicture(ra: number, dec: number, f: Field): { u: number; v: number } {
  const width = f.size * PIXEL_ARCSEC;
  let dra = ra - f.ra;
  if (dra > 180) dra -= 360;
  if (dra < -180) dra += 360;
  const east = dra * Math.cos(f.dec * RAD) * 3600;
  const north = (dec - f.dec) * 3600;
  return { u: 0.5 - east / width, v: 0.5 - north / width };
}

/** "17h 57m 48.5s" */
export function formatRa(ra: number): string {
  const h = ra / 15;
  const hh = Math.floor(h);
  const m = (h - hh) * 60;
  const mm = Math.floor(m);
  const ss = (m - mm) * 60;
  return `${hh}h ${String(mm).padStart(2, "0")}m ${ss.toFixed(1).padStart(4, "0")}s`;
}

/** "+04° 41′ 36″" */
export function formatDec(dec: number): string {
  const sign = dec < 0 ? "−" : "+";
  const a = Math.abs(dec);
  const dd = Math.floor(a);
  const m = (a - dd) * 60;
  const mm = Math.floor(m);
  const ss = Math.round((m - mm) * 60);
  return `${sign}${String(dd).padStart(2, "0")}° ${String(mm).padStart(2, "0")}′ ${String(ss).padStart(2, "0")}″`;
}

/** Small-angle separation in arcseconds. */
export function separationArcsec(ra1: number, dec1: number, ra2: number, dec2: number): number {
  let dra = ra1 - ra2;
  if (dra > 180) dra -= 360;
  if (dra < -180) dra += 360;
  return Math.hypot(dra * Math.cos(((dec1 + dec2) / 2) * RAD), dec1 - dec2) * 3600;
}
