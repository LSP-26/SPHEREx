// The acknowledgement and DOIs IRSA asks for when SPHEREx data are shown.
// Source: IRSA's "SPHEREx Quick Release (QR) Overview" page
// (https://irsa.ipac.caltech.edu/data/SPHEREx/docs/overview_qr.html), checked 2026-11-04, DOI list rechecked 2026-11-06.
// The same wording is on https://irsa.ipac.caltech.edu/Missions/spherex.html.
// Keep the text word for word: it is a required notice, so do not edit it.

export const ACKNOWLEDGEMENT =
  "This publication makes use of data products from the Spectro-Photometer for the History of " +
  "the Universe, Epoch of Reionization and Ices Explorer (SPHEREx), which is a joint project of " +
  "the Jet Propulsion Laboratory and the California Institute of Technology, and is funded by " +
  "the National Aeronautics and Space Administration.";

/** IRSA asks users of its SPHEREx data to cite the DOI of the release. The page says "10.26131/IRSA629
 *  for QR1, 10.26131/IRSA652 for QR2, 10.26131/IRSA662 for QR3". */
export const DATA_DOIS = [
  { release: "QR1", doi: "10.26131/IRSA629" },
  { release: "QR2", doi: "10.26131/IRSA652" },
  { release: "QR3", doi: "10.26131/IRSA662" },
] as const;

export const ACK_SOURCE_URL = "https://irsa.ipac.caltech.edu/data/SPHEREx/docs/overview_qr.html";
