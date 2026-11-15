/** Modified Julian Date to "2026-03-14 09:26 UTC". */
export function mjdToUtc(mjd: number): string {
  const d = new Date((mjd - 40587) * 86_400_000);
  return `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

export function micrometres(lambda: number): string {
  return `${lambda.toFixed(lambda < 1 ? 3 : 2)} µm`;
}

/** Merged colour frames (refine job, detector 0) blend all six detectors, so they have no single
 *  wavelength and no catalogue frame id. */
export const isColour = (label: { detector: number }): boolean => label.detector === 0;

/** The wavelength as shown: a colour frame says so instead of an average over all detectors. */
export function frameWavelength(label: { detector: number; lambda_um: number }): string {
  return isColour(label) ? "all detectors (colour)" : micrometres(label.lambda_um);
}

/** True when two frames' wavelengths differ by more than 5 % (never for a colour frame, whose
 *  wavelength is an average). */
export function framesDiffer(a: { detector: number; lambda_um: number }, b: { detector: number; lambda_um: number }): boolean {
  return !isColour(a) && !isColour(b) && wavelengthsDiffer(a.lambda_um, b.lambda_um);
}

/** True when two wavelengths differ by more than 5 %, so a blink between them can flicker for
 *  reasons that are not changes in the sky. */
export function wavelengthsDiffer(a: number, b: number): boolean {
  return Math.abs(a - b) / Math.max(a, b) > 0.05;
}

export function megabytes(bytes: number): string {
  return `${(bytes / 1e6).toFixed(bytes < 1e7 ? 1 : 0)} MB`;
}
