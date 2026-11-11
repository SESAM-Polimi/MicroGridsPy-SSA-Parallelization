"""Rewrite the household tier columns (h_tier1..5) of existing samples for a new household-size table.

Used once (11 Nov 2026) to move the 39 samples in input_generator/sample from the household
sizes of the 2022 JRC report (Appendix A) to UN DESA (2022) without rerunning the whole
generator. Households per cluster = round(pop_sum / household size), split into tiers with the
generator's own allocate_households() and HDI adjustment, so a full rerun gives the same numbers.

    python input_generator/code/patch_household_tiers.py input_generator/code/input_generator.py \
        input_generator/sample SIZE_TABLE [--write]

SIZE_TABLE is a CSV with columns iso3 and household_size. Without --write the script only
reports. Run it first with the table the samples were made with: it must reproduce every tier
value exactly (0 rows changed), which shows the patch follows the generator.
"""
import csv
import importlib.util
import io
import sys
from pathlib import Path

import pandas as pd

gen_path, sample_dir, size_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
write = "--write" in sys.argv

spec = importlib.util.spec_from_file_location("input_generator", gen_path)
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

hdi = pd.read_csv(gen.HDI_PATH)
lo, hi = hdi.hdi_value.min(), hdi.hdi_value.max()
adjustment = {r.country: 2 * (r.hdi_value - lo) / (hi - lo) - 1 for r in hdi.itertuples()}
sizes = pd.read_csv(size_path).set_index("iso3")["household_size"].to_dict()

TIERS = gen.TIER_COLUMNS
total_old = total_new = 0
for f in sorted(sample_dir.glob("*_sample.csv")):
    iso = f.name[:3]
    text = f.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    rows = list(csv.reader(io.StringIO(text, newline="")))
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator=newline).writerows(rows)
    if buf.getvalue() != text:
        sys.exit(f"{f.name}: csv round trip is not byte-identical, refusing to rewrite")
    header, body = rows[0], rows[1:]
    col = {c: header.index(c) for c in ("cat", "pop_sum", "rwi", "rwi_std", *TIERS)}
    changed = hh_old = hh_new = 0
    for r in body:
        if r[col["cat"]].startswith("SCHOOL"):
            continue
        households = int(round(float(r[col["pop_sum"]]) / sizes[iso]))
        tiers = gen.allocate_households(r[col["rwi"]], r[col["rwi_std"]], households, adjustment[iso])
        new = [repr(float(t)) for t in tiers]          # pandas wrote the tiers as floats: "128.0"
        old = [r[col[t]] for t in TIERS]
        hh_old += sum(float(v) for v in old); hh_new += households
        if new != old:
            changed += 1
            for t, v in zip(TIERS, new):
                r[col[t]] = v
    total_old += hh_old; total_new += hh_new
    if write and changed:
        out = io.StringIO(newline="")
        csv.writer(out, lineterminator=newline).writerows([header] + body)
        f.write_bytes(out.getvalue().encode("utf-8"))
    print(f"{iso}: size {sizes[iso]:>5}, {changed:>6} clusters changed, households {hh_old:>12,.0f} -> {hh_new:>12,.0f}",
          flush=True)
print(f"\nall: households {total_old:,.0f} -> {total_new:,.0f} ({total_new / total_old - 1:+.1%})")
