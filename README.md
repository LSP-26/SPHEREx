# Tombaugh

**A multi-epoch viewer for NASA SPHEREx.** Type any star or coordinates, and Tombaugh blinks NASA SPHEREx's
pictures of that patch of sky, taken months apart, so stars that move, flicker or fade jump out.

Made for the 2026 NASA Space Apps Challenge (SPHEREx sky-change viewer challenge). Named after Clyde Tombaugh, who
discovered Pluto in 1930 by blinking two photographic plates of the same field taken six days apart.

## What it does

SPHEREx maps the whole sky every six months in 102 colours of near-infrared light, with six detector
arrays, each seeing its own slice of the spectrum (0.75 to 5 µm). A spot of sky is seen by the same
detector at about the same wavelength on every pass. Tombaugh uses that:

- **Blink, one detector at a time.** Every look SPHEREx took of the spot with one detector, in time order,
  lined up north-up on the same grid. Only the sky changes between frames, not the instrument.
- **Difference.** Each look minus the look furthest from it in time, after removing each frame's own
  sky level and noise. Unchanged stars turn grey; a moving star leaves a white spot (now) beside a dark
  one (then).
- **Colour.** One false-colour picture per pass over the sky: blue 0.75 to 1.6 µm, green 1.6 to 3.8 µm,
  red 3.8 to 5 µm. Cold brown dwarfs, faint at short wavelengths, look red.
- **Playback.** Step through the looks or play them at 0.5 to 4 frames per second.
- **Is it known?** Gaia DR3 stars are ringed at each frame's date (moved by their proper motion);
  stars faster than 0.5″ a year are marked separately and listed with speed and distance. Known
  asteroids and comets at each frame's date come from IMCCE SkyBoT (asked from the browser).
- **Flagged pixels.** Tick *Flagged pixels* (Blink view of one detector) to colour where the SPHEREx pipeline
  flagged the frame on show: saturated, around a bright star, artefacts, detector defects. A legend gives each
  group's share of the field. The picture still shows flagged pixels by their value; measurements leave them out.
- **Inspect a pixel.** Click a pixel for its coordinates and the nearest Gaia star, a SIMBAD link
  (Harvard CfA mirror), a copyable report, and, with the server running, photometry in every SPHEREx
  frame covering it: a light curve (one point per visit, median with range) and a spectrum with the
  bands of common molecules (H₂O, CO, CH₄, CO₂, ices) marked.
- **Light or dark.** A System / Light / Dark switch in the header, remembered per browser.
- **Links and embeds.** The URL is the view (`?ex=barnard&band=2&mode=diff`,
  `?ra=133.79&dec=-7.24&fov=10`). Add `embed=1` for an `<iframe>` with just the viewer.

Search accepts a name (resolved with SIMBAD's Harvard CfA mirror, then moved by its proper motion to
2026: Barnard's Star is 270″ from its year-2000 position) or coordinates in degrees or sexagesimal, and
any field of view from 1′ to 120′ (the page shows how much data that reads).

## The five challenge targets

Each is recorded (frames, events and Gaia stars under `frontend/public/showcases/`), so it plays with no
server at all.

| Target | What Tombaugh shows |
|---|---|
| Barnard's Star | Moves north by 1 to 2 pixels between passes (checked in five of the six detectors); Difference leaves a clean white/dark pair while the field stars cancel. Gaia: 10.39″/yr, 6.0 light-years. |
| Epsilon Indi B | The brown dwarf pair at the centre. Motion 4.7″/yr, under a pixel per pass (subtle by eye; the backend measured 4.51″/yr at 39σ with `scripts/centroid_track.py`). |
| WISE 0855 | Field centred on its 2026 position. At 6″ pixels it blends with a G 16.3 background star 7″ away; we say so rather than claim a detection. |
| R Draconis | Mira variable: brightness over time from 2,358 frame measurements, shown per detector, plus its spectrum. |
| RW Aurigae | Young variable star: brightness over time (320 frame measurements) and spectrum. |

## How it works

```
browser (React, frontend/)                       server (Python, backend/)
  search → page URL ─── POST /flipbook ────────►  catalogue index of SPHEREx frames (IRSA TAP)
  blink / difference / colour (canvas)  ◄─ events + PNG frames ─  byte-range reads from IRSA's S3 bucket,
  Gaia rings ◄─────────── GET /overlay ────────   clean, regrid north-up, render
  measure a spot ◄──────── GET /measure ───────   aperture photometry on every frame there
```

- The server finds every SPHEREx Level 2 frame covering the position, reads only the needed rows of each
  file straight from IRSA's cloud copy (HTTP range requests, no full downloads), cleans flagged pixels,
  regrids north-up and streams one frame per visit and detector as it is ready.
- The page groups frames by detector and time, and does the difference and colour maths in the browser.
- New data: `python -m backend.offline.refresh --out .cache/catalogue` adds frames from new SPHEREx
  releases (run it weekly); no code change is needed for a new release.

## Run it

Frontend only, against the shared server whose address is in `frontend/remote-api.txt`:

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. Tests: `npm test`. Production build: `npm run build` (reads
`remote-api.txt`; set `TOMBAUGH_BASE=/some/path/` if the site is served from a sub-path).

### Publish the site (the repository stays private)

The site is static files, so any static host works. With Cloudflare Pages (free):
Workers & Pages → Create → Pages → Connect to Git → pick this repository (Cloudflare's GitHub app
can be given access to just this private repository), then:

| Setting | Value |
|---|---|
| Production branch | `main` |
| Root directory | `frontend` |
| Build command | `npm run build` |
| Build output directory | `dist` |
| Environment variable | `NODE_VERSION` = `22` |

The site is public at `https://<project>.pages.dev`; the code stays private. Every push to `main`
rebuilds it, including the commits that update `remote-api.txt`, so the page always calls the current
server. The recorded examples are part of the site and work with the server off.

Your own server (needs the frame catalogue in `.cache/catalogue`, about 1 to 2 GB of RAM):

```bash
pip install -e ".[dev]"
python -m backend.server --source catalogue
```

`SPHEREX_REFINE=1` also stacks every frame of a visit per detector (deeper pictures, slower).
Backend tests: `pytest`.

## Data and credits

- NASA SPHEREx Level 2 spectral images, NASA/IPAC Infrared Science Archive (IRSA):
  https://irsa.ipac.caltech.edu/Missions/spherex.html (QR1 doi:10.26131/IRSA629, QR2
  doi:10.26131/IRSA652, QR3 doi:10.26131/IRSA662)
- ESA Gaia DR3 (ESA archive, GAVO or VizieR mirrors) for star positions, motions and distances
- SIMBAD (CDS, via the Harvard CfA mirror) for names; IMCCE SkyBoT for known asteroids and comets
- Libraries: React, Vite, Tailwind CSS, FastAPI, NumPy, SciPy, Astropy, reproject, DuckDB, PyArrow, httpx

This publication makes use of data products from the Spectro-Photometer for the History of the Universe,
Epoch of Reionization and Ices Explorer (SPHEREx), which is a joint project of the Jet Propulsion
Laboratory and the California Institute of Technology, and is funded by the National Aeronautics and
Space Administration. Tombaugh is an independent project, not endorsed by NASA.

## Layout

`frontend/` the site (React + Vite) · `backend/` pipeline, data adapters, jobs, API server, offline builders ·
`contracts/` shared models and the API schema · `scripts/` checks run against real data · `fixtures/`
small synthetic frames for tests.
