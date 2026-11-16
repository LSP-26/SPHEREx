// The five targets of the challenge, Each one has a recorded replay under
// /showcases/<key>/ (frames, events and Gaia stars), so it plays with no server at all.
// Positions are where the objects are in 2026 (moved by proper motion), not their J2000 positions.

export interface Example {
  key: string;
  name: string;
  /** Other names a person might type in the search box. */
  aliases: string[];
  kind: string;
  /** One line under the card. */
  hook: string;
  /** Band to open on (detector 1 to 6), when one shows the object best. */
  band?: number;
  mode?: "blink" | "diff";
}

export const EXAMPLES: Example[] = [
  {
    key: "barnard",
    name: "Barnard's Star",
    aliases: ["barnard", "barnards star", "barnard star", "gj 699", "v2500 oph"],
    kind: "High proper-motion red dwarf",
    hook: "6.0 light-years; proper motion 10.4″/yr, the largest of any star.",
    band: 2,
  },
  {
    key: "eps_indi_b",
    name: "Epsilon Indi B",
    aliases: ["eps indi b", "epsilon indi b", "eps ind b", "epsilon indi ba", "epsilon indi bb"],
    kind: "Binary brown dwarf (T1 + T6)",
    hook: "11.8 light-years; proper motion 4.7″/yr.",
    band: 5,
  },
  {
    key: "wise0855",
    name: "WISE 0855",
    aliases: ["wise 0855", "wise0855", "wise 0855-0714", "wise j085510.83-071442.5"],
    kind: "Y-type brown dwarf",
    hook: "7.4 light-years; the coldest known brown dwarf (about 250–285 K); proper motion 8.1″/yr.",
    band: 6,
  },
  {
    key: "r_dra",
    name: "R Draconis",
    aliases: ["r dra", "r draconis"],
    kind: "Mira variable",
    hook: "Pulsating red giant with a period of about 245 days.",
  },
  {
    key: "rw_aur",
    name: "RW Aurigae",
    aliases: ["rw aur", "rw aurigae"],
    kind: "Young variable binary (T Tauri type)",
    hook: "Accreting young star with irregular dimming by circumstellar dust.",
  },
];

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9+-]+/g, " ").trim();

/** An example whose name or alias matches what was typed. */
export function findExample(text: string): Example | undefined {
  const q = norm(text);
  if (!q) return undefined;
  return EXAMPLES.find((e) => norm(e.name) === q || e.aliases.some((a) => norm(a) === q));
}
