// Turn an object name ("Barnard's Star") into coordinates with the SIMBAD TAP service, straight
// from the browser. Needs the internet, not our server. SIMBAD gives J2000 positions plus proper motion; lib/search.ts moves the star to today.
//
// Only the CfA (Harvard) mirror is used: the CDS (Strasbourg) host has been unreachable for weeks
// (connections time out), and waiting for it would stall every search.

const TAPS = ["https://simbad.cfa.harvard.edu/simbad/sim-tap/sync"];

export type Resolved =
  | { ok: true; name: string; ra: number; dec: number; pmra?: number | null; pmdec?: number | null }
  | { ok: false; error: string };

/** Quote a name for an ADQL string literal. */
export function adqlString(s: string): string {
  return `'${s.replace(/'/g, "''")}'`;
}

export async function resolveName(name: string, fetchFn: typeof fetch = fetch, signal?: AbortSignal): Promise<Resolved> {
  const q = name.trim();
  if (!q) return { ok: false, error: "Type a name first." };
  const query = `SELECT TOP 1 main_id, ra, dec, pmra, pmdec FROM basic JOIN ident ON oid = oidref WHERE id = ${adqlString(q)}`;
  const params = new URLSearchParams({ request: "doQuery", lang: "adql", format: "json", query });
  let body: { data?: [string, number, number, number | null, number | null][] } | null = null;
  for (const tap of TAPS) {
    try {
      const res = await fetchFn(`${tap}?${params}`, { signal: signal ?? AbortSignal.timeout(12_000) });
      if (!res.ok) continue;
      body = await res.json();
      break;
    } catch {
      if (signal?.aborted) break;
    }
  }
  if (!body) return { ok: false, error: "Could not reach the name service. Type the coordinates instead." };
  const row = body.data?.[0];
  if (!row || typeof row[1] !== "number" || typeof row[2] !== "number") {
    return { ok: false, error: `No object called "${q}" was found.` };
  }
  const pm = (x: unknown) => (typeof x === "number" && Number.isFinite(x) ? x : null);
  return { ok: true, name: row[0], ra: row[1], dec: row[2], pmra: pm(row[3]), pmdec: pm(row[4]) };
}
