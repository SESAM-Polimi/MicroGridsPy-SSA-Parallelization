"""Rewrite the `cooling` column of existing samples with the Met Office CDD rule.

Used once (11 Nov 2026) to update the 39 samples in input_generator/sample without rerunning the
whole generator, whose LFS inputs lack the Tmin/Tmax rasters. A full rerun of input_generator.py
with the 30 arc-sec Tmin, Tavg and Tmax rasters gives the same classes.

Only the `cooling` field of population clusters (GHSL_*, SPARSE_*) changes; every other byte of
each CSV stays as it is (schools keep "NA"). The classification functions are imported from the
generator on branch cooling-met-office, so the samples and the script cannot drift apart.

    python input_generator/code/patch_cooling_column.py input_generator/code/input_generator.py \
        input_generator/sample "wc30s/ssa_{var}_{month:02d}.tif" [--write] [--report DIR]

RASTER_PATTERN has {var} and {month}: WorldClim 2.1 monthly tmin/tavg/tmax at 30 arc-sec
(1970-2000), here cropped to lon -20..55, lat -36..28. Without --write it only reports what would
change; --report DIR writes per-country old/new classes and a summary there.
"""
import csv
import importlib.util
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

gen_path, sample_dir, pattern = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
write = "--write" in sys.argv
report = Path(sys.argv[sys.argv.index("--report") + 1]) if "--report" in sys.argv else None
if report:
    report.mkdir(parents=True, exist_ok=True)

spec = importlib.util.spec_from_file_location("input_generator", gen_path)
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def nearest_valid(stack, row, col, max_radius=50):
    """Value at the nearest cell (in pixels) valid in all 12 months, searching outwards."""
    valid = np.isfinite(stack).all(axis=0)
    for r in range(1, max_radius + 1):
        r0, r1 = max(row - r, 0), min(row + r + 1, valid.shape[0])
        c0, c1 = max(col - r, 0), min(col + r + 1, valid.shape[1])
        rr, cc = np.nonzero(valid[r0:r1, c0:c1])
        if rr.size:
            d2 = (rr + r0 - row) ** 2 + (cc + c0 - col) ** 2
            k = np.argmin(d2)
            return stack[:, rr[k] + r0, cc[k] + c0]
    return np.full(stack.shape[0], np.nan)


def sample(var, lon, lat):
    """(clusters, 12) monthly values; NoData -> nearest cell valid in all months."""
    out = np.full((len(lon), 12), np.nan)
    for m in range(12):
        with rasterio.open(pattern.format(var=var, month=m + 1)) as src:
            band = src.read(1).astype("float32")
            if src.nodata is not None:
                band[band == src.nodata] = np.nan
            rows, cols = rasterio.transform.rowcol(src.transform, lon, lat)
            rows = np.clip(rows, 0, src.height - 1); cols = np.clip(cols, 0, src.width - 1)
            out[:, m] = band[rows, cols]
            shape, transform = band.shape, src.transform
        del band
    missing = np.flatnonzero(~np.isfinite(out).all(axis=1))
    if missing.size:                      # read a small 12-month window around each missing point
        for i in missing:
            r, c = rasterio.transform.rowcol(transform, lon[i], lat[i])
            win = rasterio.windows.Window(c - 50, r - 50, 101, 101)
            stack = []
            for m in range(12):
                with rasterio.open(pattern.format(var=var, month=m + 1)) as src:
                    a = src.read(1, window=win, boundless=True, fill_value=src.nodata).astype("float32")
                    if src.nodata is not None:
                        a[a == src.nodata] = np.nan
                    stack.append(a)
            out[i] = nearest_valid(np.stack(stack), 50, 50)
    return out, missing.size


summary = []
for f in sorted(sample_dir.glob("*_sample.csv")):
    raw = f.read_bytes()
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    rows = list(csv.reader(io.StringIO(text, newline="")))
    header, body = rows[0], rows[1:]
    # round-trip check: the csv module must reproduce the file exactly before we touch it
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator=newline).writerows(rows)
    if buf.getvalue() != text:
        sys.exit(f"{f.name}: csv round trip is not byte-identical, refusing to rewrite")
    i_cat, i_lat, i_lon, i_cool = (header.index(c) for c in ("cat", "lat", "lon", "cooling"))
    pop = [k for k, r in enumerate(body) if not r[i_cat].startswith("SCHOOL")]
    lon = np.array([float(body[k][i_lon]) for k in pop]); lat = np.array([float(body[k][i_lat]) for k in pop])
    t = {}; nfill = {}
    for v in ("tmin", "tavg", "tmax"):
        t[v], nfill[v] = sample(v, lon, lat)
    if not all(np.isfinite(t[v]).all() for v in t):
        sys.exit(f"{f.name}: missing climate data after nearest-cell fallback")
    new = gen.cooling_class(gen.monthly_cdd(t["tmin"], t["tavg"], t["tmax"]))
    old = np.array([body[k][i_cool] for k in pop])
    for k, c in zip(pop, new):
        body[k][i_cool] = c
    iso = f.name[:3]
    summary.append({"iso": iso, "n": len(pop), "changed": int((old != new).sum()),
                    "fallback": max(nfill.values()),
                    **{f"old_{c}": int((old == c).sum()) for c in ("AY", "AS", "OM", "NC")},
                    **{f"new_{c}": int((new == c).sum()) for c in ("AY", "AS", "OM", "NC")}})
    if report:
        pd.DataFrame({"cat": [body[k][i_cat] for k in pop], "lat": lat, "lon": lon, "old": old, "new": new}).to_csv(
            report / f"{iso}.csv", index=False)
    if write:
        out = io.StringIO(newline="")
        csv.writer(out, lineterminator=newline).writerows([header] + body)
        f.write_bytes(out.getvalue().encode("utf-8"))
    print(f"{iso}: {len(pop):>6} clusters, {int((old != new).sum()):>6} changed, fallback {max(nfill.values())}", flush=True)

s = pd.DataFrame(summary).set_index("iso")
if report:
    s.to_csv(report / "summary.csv")
tot = s.sum()
print("\nall:", int(tot.n), "clusters,", int(tot.changed), "changed")
for c in ("AY", "AS", "OM", "NC"):
    print(f"  {c}: {100 * tot[f'old_{c}'] / tot.n:5.1f} % -> {100 * tot[f'new_{c}'] / tot.n:5.1f} %")
