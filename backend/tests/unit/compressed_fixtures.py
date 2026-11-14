"""Builds small SPHEREx-like FITS files with astropy, plain or with tile-compressed FLAGS."""
import numpy as np
from astropy.io import fits

NY, NX = 60, 80
WCS = {"NAXIS": 2, "NAXIS1": NX, "NAXIS2": NY, "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
       "CRPIX1": 40.0, "CRPIX2": 30.0, "CRVAL1": 10.0, "CRVAL2": 0.0,
       "CDELT1": -6.15 / 3600, "CDELT2": 6.15 / 3600, "CUNIT1": "deg", "CUNIT2": "deg"}


def make_flags(seed=5, unsigned=False):
    """Mostly zero with scattered bits set, like real flags (so Rice has something to do)."""
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2**20, size=(NY, NX)) * (rng.random((NY, NX)) < 0.15)
    if unsigned:
        return (bits | (rng.random((NY, NX)) < 0.05) * 2**31).astype("uint32")
    return bits.astype("int32")


def write_frame(path, *, flags=None, compression="RICE_1", tile_shape=(1, NX), drop=(),
                image_dtype="float32", extra_header=None):
    """compression=None writes FLAGS as a plain image."""
    rng = np.random.default_rng(1)
    flags = make_flags() if flags is None else flags
    image = fits.ImageHDU(rng.normal(size=(NY, NX)).astype(image_dtype), name="IMAGE")
    image.header.update(WCS)
    image.header["DETECTOR"] = 3
    if extra_header:
        image.header.update(extra_header)
    if compression is None:
        flags_hdu = fits.ImageHDU(flags, name="FLAGS")
    else:
        flags_hdu = fits.CompImageHDU(flags, name="FLAGS", compression_type=compression,
                                      tile_shape=tile_shape)
    hdus = [fits.PrimaryHDU(), image, flags_hdu,
            fits.ImageHDU(rng.random((NY, NX)).astype("float32"), name="VARIANCE"),
            fits.ImageHDU(rng.random((NY, NX)).astype("float32"), name="ZODI"),
            fits.ImageHDU(rng.random((5, 9, 9)).astype("float32"), name="PSF")]
    hdus = [h for h in hdus if h.name not in drop]
    fits.HDUList(hdus).writeto(path, overwrite=True)
    return flags
