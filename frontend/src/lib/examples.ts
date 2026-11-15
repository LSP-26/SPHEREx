// The five targets of the challenge, with what to look for. Each one has a recorded replay under
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
  /** What to look for, shown beside the viewer. */
  lookFor: string;
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
    lookFor:
      "The bright star in the middle creeps north between looks while the rest stay put. " +
      "In Difference the others cancel and it leaves a white (now) and dark (then) pair.",
    band: 2,
  },
  {
    key: "eps_indi_b",
    name: "Epsilon Indi B",
    aliases: ["eps indi b", "epsilon indi b", "eps ind b", "epsilon indi ba", "epsilon indi bb"],
    kind: "Binary brown dwarf (T1 + T6)",
    hook: "11.8 light-years; proper motion 4.7″/yr.",
    lookFor:
      "The pair is at the centre. It moves under one pixel between passes, so the shift is subtle. " +
      "Compare the first and last looks in Difference.",
    band: 5,
  },
  {
    key: "wise0855",
    name: "WISE 0855",
    aliases: ["wise 0855", "wise0855", "wise 0855-0714", "wise j085510.83-071442.5"],
    kind: "Y-type brown dwarf",
    hook: "7.4 light-years; the coldest known brown dwarf (about 250–285 K); proper motion 8.1″/yr.",
    lookFor:
      "Centred on where it is in 2026, over 2 arcminutes from the challenge coordinates. The hardest target: at 6″ pixels " +
      "it blends with a background star 7″ away (click the centre; Gaia knows it). The brown dwarf is invisible below 1 µm, " +
      "so it could only add light in the longest band. Compare 0.7 µm with 4.6 µm. This is a limit of the survey, not a detection.",
    band: 6,
  },
  {
    key: "r_dra",
    name: "R Draconis",
    aliases: ["r dra", "r draconis"],
    kind: "Mira variable",
    hook: "Pulsating red giant with a period of about 245 days.",
    lookFor:
      "The central star swells and shrinks from look to look. The chart below follows it through every frame. " +
      "It saturates the detectors, so the true peak is brighter than shown.",
  },
  {
    key: "rw_aur",
    name: "RW Aurigae",
    aliases: ["rw aur", "rw aurigae"],
    kind: "Young variable binary (T Tauri type)",
    hook: "Accreting young star with irregular dimming by circumstellar dust.",
    lookFor:
      "The central star changes brightness from look to look while the field stars stay steady. " +
      "The chart below has every measurement.",
  },
];

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9+-]+/g, " ").trim();

/** An example whose name or alias matches what was typed. */
export function findExample(text: string): Example | undefined {
  const q = norm(text);
  if (!q) return undefined;
  return EXAMPLES.find((e) => norm(e.name) === q || e.aliases.some((a) => norm(a) === q));
}
