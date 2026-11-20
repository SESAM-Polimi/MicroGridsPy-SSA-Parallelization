"""Download the WorldPop layer that input_generator.py reads, into the inputs folder.

    python sample_generation/fetch_worldpop.py AGO SLE ... --inputs /path/to/input_generator

Writes {inputs}/Input/{ISO}/{iso}_pop_{year}_CN_1km_R2025A_UA_v1.tif for each country:
WorldPop Global2 release R2025A, constrained, UN-adjusted, 1 km (Bondarenko et al. 2025,
doi 10.5258/SOTON/WP00840). The year is WORLDPOP_YEAR in input_generator.py, so the two scripts
cannot disagree. Files already present are kept; pass --force to download them again.
"""
import argparse
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from input_generator import INPUTS_ENV, WORLDPOP_YEAR, CountryInputs, worldpop_filename  # noqa: E402

BASE_URL = "https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A"
TIFF_MAGIC = (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")   # TIFF and BigTIFF, both byte orders


def worldpop_url(country: str, year: int = WORLDPOP_YEAR) -> str:
    iso = country.upper()
    return f"{BASE_URL}/{year}/{iso}/v1/1km_ua/constrained/{worldpop_filename(iso, year)}"


def download(url: str, target: Path) -> int:
    """Download to target.part, check it is a TIFF, then rename. A failed or interrupted
    download never leaves a file under the final name, so missing() still reports it."""
    partial = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response, open(partial, "wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    with open(partial, "rb") as f:
        if f.read(4) not in TIFF_MAGIC:
            partial.unlink()
            raise ValueError(f"{url} did not return a GeoTIFF")
    partial.replace(target)
    return target.stat().st_size


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("countries", nargs="+", help="ISO3 codes, e.g. AGO SLE")
    parser.add_argument("--inputs", type=Path, default=os.environ.get(INPUTS_ENV),
                        help=f"folder with the source inputs (default: ${INPUTS_ENV})")
    parser.add_argument("--force", action="store_true", help="download files already present")
    args = parser.parse_args(argv)
    if args.inputs is None:
        parser.error(f"give --inputs or set ${INPUTS_ENV}")
    for country in (c.upper() for c in args.countries):
        target = CountryInputs.for_country(args.inputs, country).worldpop
        if target.exists() and not args.force:
            print(f"{country}: kept {target}")
            continue
        size = download(worldpop_url(country), target)
        print(f"{country}: {size / 1e6:.1f} MB -> {target}")


if __name__ == "__main__":
    main()
