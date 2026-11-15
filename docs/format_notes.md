# SPHEREx L2 file format notes (X0)

Measured 2026-11 on two real frames: `level2_2025W17_4B_0001_1D2_spx_l2b-v20-2025-240` (detector 2,
pipeline v20) and `level2_2026W30_1B_0001_1D1_spx_l2b-v27-2026-222` (detector 1, v27).

## Units and time
- IMAGE, VARIANCE, ZODI: `MJy / sr` (IMAGE has BUNIT; the solid-angle map for Jy/pixel is under
  `qr2/solid_angle_pixel_map/`).
- FLAGS: dimensionless int32 bit field.
- Times are `TIMESYS = UTC`, `MJDREF = 0`, so `MJD-AVG` is a plain UTC MJD. Use MJD-AVG as the
  frame time (exposure is about 118 s: MJD-BEG to MJD-END).
- `DETECTOR` header card: 1 to 3 are SWIR, 4 to 6 are MWIR.

## FLAGS bits (names from the MSKN cards in the v27 FLAGS header)
| bit | name | meaning | in clean.py |
|---|---|---|---|
| 0 | TRANSIENT | transient detected during SUR | fatal |
| 1 | OVERFLOW | overflow during SUR = **saturation** | fatal, reported as "saturated" |
| 2 | SUR_ERROR | onboard processing error | fatal |
| 6 | NONFUNC | permanently unusable | fatal |
| 7 | DICHROIC | low efficiency, dichroic | fatal |
| 9 | MISSING_DATA | onboard data lost | fatal |
| 10 | HOT | hot pixel | fatal |
| 11 | COLD | anomalously low signal | fatal |
| 12 | FULLSAMPLE | full sample history available | information only |
| 14 | PHANMISS | phantom correction not applied | fatal |
| 15 | NONLINEAR | nonlinearity correction unreliable | fatal |
| 17 | PERSIST | persistent charge | fatal |
| 19 | OUTLIER | flagged by outlier detection | fatal |
| 20 | CROSSTALK | crosstalk | not fatal |
| 21 | SOURCE | mapped to a known source (45 percent of a v27 frame) | not fatal |
| 22 | GHOST | bright source inside the exposure | fatal |
| 23 | GHOST_FPA | bright source on another detector | not fatal, not seen yet |
| 24 | GHOST_EXT | bright source outside the field | fatal |
| 25 | STREAK | streak | not fatal, not seen yet |
| 26 | BLOOM | bloom artifact (around saturated sources) | fatal |
| 27 | SNOWBALL | snowball artifact | fatal |
| 28 | HALO | transient halo | fatal |

The v20 header has no MSKN cards, but the header's per-flag counts (N_OVERFLOW 279, N_SOURCE
822376, N_PERSIST 1532, ...) equal the bit counts measured in that frame, so the numbering is
the same. Saturation check: the OVERFLOW bit count equals N_OVERFLOW in both frames and its
pixels have median IMAGE 160 to 180 MJy/sr against about 0.3 for the sky.
Bits 4, 5 and 29 are undefined in the header; clean.py keeps them fatal (SPExPI had them).

## Wavelength
- IMAGE header alternate WCS `W`: `CTYPE1W = CTYPE2W = 'WAVE-TAB'`, output axis 1 is the central
  wavelength and axis 2 the bandwidth, both in um. `PS1_0W = 'WCS-WAVE'`: the table lives in the
  last extension.
- WCS-WAVE (last extension, a few KB at the end of the file): one row with `X`, `Y` control
  points (1-based pixels, 11 x 11 points in v20, 9 x 9 in v27) and `VALUES` of shape
  (2, ny, nx). Header cards `DETECTOR` and `DETCOORD = 'sky'`; HISTORY names the calibration file
  (`spectral_wcs_1_v2.2.fits` in the v27 frame).
- astropy: `WCS(image_header, key='W', naxis=2, fobj=hdul).all_pix2world([[x, y]], 0)`
  returns (wavelength, bandwidth). Wavelength falls with y (D1: 1.13 um at y=0 to 0.74 um at
  y=2039, D2: 1.67 to 1.09 um) and changes by only about 0.01 um across x.
- Bandwidth is about 0.025 to 0.04 um, which is about lambda / 40, consistent with R of about 40 below 3.8 um.
- Code: `backend/adapters/wavelength.py`.
- Table stability (check_wavelength.py, 2026-11-05, 286 groups): within one (detector, version)
  the table is identical for every frame. Across versions D1 to D4 never change, and D5 and D6
  change by 6.4 nm at v27 (new calibration file). Key tables on (detector, version).

## Extensions
v20: PRIMARY, IMAGE, FLAGS, VARIANCE, ZODI, PSF, WCS-WAVE. v27: FLAGS is a CompImageHDU
(RICE_1), PSF is called EPSF (a BINTABLE). WCS-WAVE is last in both.

## Solid-angle pixel map (T7, probed 2026-11)
- Path: `s3://nasa-irsa-spherex/qr2/solid_angle_pixel_map/cal-sapm-v2-2025-164/<d>/solid_angle_pixel_map_D<d>_spx_cal-sapm-v2-2025-164.fits`
  for detector d = 1 to 6. All six detectors were read.
- One map per detector, not per exposure. PRIMARY (empty) + IMAGE, 2040 x 2040, BITPIX -32
  (big-endian float32), uncompressed, 16,652,160 bytes. So a row band is one contiguous byte range,
  like the frame planes.
- **BUNIT is `arcsec2`, not steradians.** Median 37.86 arcsec^2 per pixel (D1, D3) and 37.69 (D2),
  against 37.82 for a 6.15 arcsec square pixel. No NaNs.
- It varies smoothly across the detector: min to max 37.46 to 38.38 on D1, 2.4 percent. D2 1.7
  percent. Read it at the star's own pixel; a constant would add up to about 1.2 percent of error.
- Header: `ARRAY` = detector number, `DETCOORD = 'sky'` ("MWIR pixels flipped on X to match SWIR
  view", the same convention as the frame files), `ORIGIN = SSDC`, `DATE = 2025-06-06`.
- D4 and D6 (read 2026-11) have the same format, units (arcsec2) and size. D1/D4 and D3/D6
  share the same pattern across the detector, which fits the X flip being already applied.
  Checked with `scripts/check_sapm_alignment.py`: map pixel (x, y) is the same pixel as
  IMAGE pixel (x, y), for a SWIR and an MWIR detector, so no flip is needed.
- QR3 has its own folder, `qr3/solid_angle_pixel_map/` (opened 2026-11-07: identical to QR2 on all six detectors, see the comparison section below). `qr3/spectral_wcs/` and `qr3/sip/` exist too (the D5/D6 wavelength change at v27
  probably comes from the first).
- Conversion: 1 arcsec^2 = 2.3504e-11 sr, so
  `flux_Jy_per_pixel = IMAGE_MJy_per_sr * area_arcsec2 * 2.3504e-5`
  (that is 1e6 * 2.3504e-11). The flux of a source is the sum of this over its aperture.
- Cache one map row band per (detector, aperture rows); the whole set is 100 MB, so the full
  maps may also simply be kept on disk.

## QR3 solid-angle map compared with QR2 (2026-11-07)
This supersedes the earlier bullet about `qr3/solid_angle_pixel_map/` ("listed, contents not
opened") and any note that QR3 needs a different map.
- `scripts/x7_compare.py` read both maps of D1 (SWIR) and D4 (MWIR):
  QR2 `cal-sapm-v2-2025-164` and QR3 `cal-sapm-v3-2026-191`. Both are arcsec2, 2040 x 2040.
- **The v3 map equals v2.** Ratio v3 / v2 is 1.00000 at every pixel (largest difference 0.000%),
  pattern correlation 1.000000. Median area 37.8604 (D1) and 37.9329 (D4) arcsec^2 in both.
- So a QR2 to QR3 light curve (R Dra, RW Aur) gets no step from the solid angle.
- D2, D3, D5 and D6 were compared on 2026-11-08: also identical. All six detectors confirmed.
- `backend/adapters/solid_angle.py` keeps the (release, detector) key anyway, so an unknown
  release fails loudly.

## Photometry and light curves (T8, 2026-11-08)
- Flux (Jy) = sum over the aperture of (IMAGE - background) x pixel area (arcsec^2) x 2.3504e-5.
  Pixel area from `backend/adapters/solid_angle.py`, keyed by (release, detector). QR2 and QR3
  maps are identical on D1 and D4 (ratio 1.00000).
- `aperture_photometry`: r 2 px, sky annulus 2 to 3 x r, error from VARIANCE plus a linear
  background term. A saturated aperture gives a LOWER LIMIT (saturated pixels count as zero).
- Bright stars (R Dra, Barnard) have a flagged ring round the saturated core, so the 2 px
  aperture is empty. `large_aperture_photometry` then sums an 8 px aperture over every pixel
  that has a number (flagged or not), with sky from 15 px out. Still a lower limit.
- Every point is one frame at its own wavelength and date. Never join points across detectors.
  The spectrum mixes dates, so a variable star scatters. Both are indicative.
- R Dra: 2,356 of 2,358 points are lower limits. Its light curve is a floor.

## Bright detector bands (T9b, 2026-11-07)
- DICHROIC (flagged): D3 rows 0 to about 61, D4 rows about 1969 to 2039, in every version.
- Unflagged bright bands: D1 rows about 60 to 270, D2 rows about 1985 to 2039 (`BRIGHT_BANDS`
  in `clean.py`). D5 and D6 show none.
- 12 v20 frames per detector: the band is in the same rows every time (shape r 1.00 and 0.999)
  but its amplitude varies from 0.12 to 3.1 (D1) and 0.12 to 1.2 (D2) MJy/sr, and does not
  follow the residual sky level (r 0.00 and -0.31). So it is NOT a fixed calibration offset.
  T9b4 (30 frames per detector, survey-wide and one week): no correlation with time and the one-week spread equals the survey spread, so not a drift. D1 follows Moon angle, elongation and ecliptic latitude, D2 weakly. Pointing-dependent, probably scattered light. Not subtracted.
- `scripts/band_vs_geometry.py` tests time, zodi level, sun, moon and bright-source flags (result above).

## Wavelength check against nominal ranges (V1.2, 2026-11-09)
- One frame per detector (Barnard's field), x=1019, y = 0, 500, 1019, 1500, 2039. D1 1.115 to 0.735 um, D2 1.650 to 1.086, D3 2.419 to 1.616, D4 3.818 to 2.398, D5 4.418 to 3.792, D6 4.999 to 4.395.
- Matches the nominal detector ranges (from memory, confirm on IRSA's page) within 0.03 um, under one resolution element; detectors join end to end; slope about 0.022 um per 120-row channel (lambda/40).
