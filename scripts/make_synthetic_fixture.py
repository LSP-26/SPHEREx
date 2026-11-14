"""Builds fixtures/synthetic_test: 6 fake frames with one mover. For tests only, flagged synthetic."""
import json
from pathlib import Path

import numpy as np

out = Path("fixtures/synthetic_test"); out.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(0)
frames = []
for i in range(6):
    fid = f"synth_{i:02d}"
    t0 = 61000.0 + 15 * i + (100 if i >= 3 else 0)
    frames.append(dict(frame_id=fid, detector=1 + i % 2, t_start=t0, t_end=t0 + 0.01,
                       release="QR2", version="v0", uri=f"s3://synthetic/{fid}.fits"))
    img = rng.normal(1.0, 0.05, (47, 47)).astype("float32")
    img[23, 20 + i] += 20.0  # the mover
    np.savez_compressed(out / f"{fid}.npz", image=img, flags=np.zeros((47, 47), "uint32"),
                        variance=np.full((47, 47), 0.0025, "float32"), x0=0, y0=0)
(out / "meta.json").write_text(json.dumps(
    dict(name="synthetic_test", ra=10.0, dec=0.0, synthetic=True, frames=frames), indent=1))
