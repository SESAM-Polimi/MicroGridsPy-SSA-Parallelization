"""Command line and input paths of sample_generation/input_generator.py (checklist C24).

Run from the repo root:  python -m pytest tests/test_sample_generation_cli.py -q
"""
import json
import sys
from pathlib import Path

import pytest

for module in ("geopandas", "rasterio", "fiona", "scipy", "shapely"):
    pytest.importorskip(module)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sample_generation"))
import input_generator as gen   # noqa: E402


def test_paths_follow_the_country(tmp_path):
    inputs = gen.CountryInputs.for_country(tmp_path, "KEN")
    assert inputs.gadm == tmp_path / "Input" / "KEN" / "gadm41_KEN.gpkg"
    assert inputs.worldpop.name == "ken_pop_2020_CN_1km_R2025A_UA_v1.tif"
    assert inputs.rwi == tmp_path / "Global" / "rwi.tif"
    assert len(inputs.missing()) == 7                       # nothing exists yet


def test_unzipped_ghsl_counts_as_present(tmp_path):
    inputs = gen.CountryInputs.for_country(tmp_path, "KEN")
    inputs.ghsl.with_suffix("").parent.mkdir(parents=True)
    inputs.ghsl.with_suffix("").write_bytes(b"x")
    assert str(inputs.ghsl) not in inputs.missing()


def test_missing_inputs_name_the_country_and_the_files(tmp_path):
    with pytest.raises(FileNotFoundError, match=r"(?s)KEN: missing required inputs:\n.*gadm41_KEN.gpkg"):
        gen.main(["ken", "--inputs", str(tmp_path)])


def test_no_input_folder_is_an_error(monkeypatch):
    monkeypatch.delenv(gen.INPUTS_ENV, raising=False)
    with pytest.raises(SystemExit):
        gen.main(["KEN"])


def test_provenance_lists_every_existing_input(tmp_path):
    inputs = gen.CountryInputs.for_country(tmp_path, "KEN")
    inputs.gadm.parent.mkdir(parents=True)
    inputs.gadm.write_bytes(b"boundaries")
    out = tmp_path / "out" / "KEN" / "advanced_sample.csv"
    out.parent.mkdir(parents=True)
    record = json.loads(gen.write_provenance(inputs, out, rows=3).read_text())
    assert record["country"] == "KEN" and record["rows"] == 3
    assert record["inputs"]["gadm"]["bytes"] == 10
    assert record["inputs"]["gadm"]["sha256"] == gen.sha256(inputs.gadm)
    assert {"hdi_table", "household_size_table"} <= set(record["inputs"])
    assert "worldpop" not in record["inputs"]                 # absent files are not listed
