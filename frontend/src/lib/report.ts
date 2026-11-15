// The text copied by "Copy report": everything someone needs to check a possible discovery.
import { formatDec, formatRa } from "./sky";

export interface ReportInput {
  ra: number;
  dec: number;
  link: string;
  band: string;
  looks: string[];
  known: string | null;
}

export function simbadUrl(ra: number, dec: number, radiusArcsec = 15): string {
  const q = new URLSearchParams({
    Coord: `${ra.toFixed(5)} ${dec >= 0 ? "+" : ""}${dec.toFixed(5)}`,
    CooFrame: "ICRS",
    Radius: String(radiusArcsec),
    "Radius.unit": "arcsec",
  });
  return `https://simbad.cfa.harvard.edu/simbad/sim-coo?${q}`;
}

export function reportText(r: ReportInput): string {
  return [
    "Tombaugh report: something worth a second look in SPHEREx data",
    `Position: RA ${r.ra.toFixed(5)}°, Dec ${r.dec >= 0 ? "+" : ""}${r.dec.toFixed(5)}° (${formatRa(r.ra)}, ${formatDec(r.dec)})`,
    `Band: ${r.band}`,
    `Looks compared: ${r.looks.join(", ")}`,
    `Known star here (Gaia DR3): ${r.known ?? "none brighter than the catalogue shown"}`,
    `See it: ${r.link}`,
    `Check SIMBAD: ${simbadUrl(r.ra, r.dec)}`,
  ].join("\n");
}
