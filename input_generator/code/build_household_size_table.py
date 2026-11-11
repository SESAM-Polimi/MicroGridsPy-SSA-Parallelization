"""Build Global/household_size_undesa2022.csv from the UN DESA household size database.

Source: United Nations, Department of Economic and Social Affairs, Population Division (2022).
Database on Household Size and Composition 2022. CC BY 3.0 IGO.
https://www.un.org/development/desa/pd/sites/www.un.org.development.desa.pd/files/undesa_pd_2022_hh-size-composition.xlsx

For each country we take the most recent observation of "Average household size (number of
members)" (usual residents per household). The database has national values only.

    python input_generator/code/build_household_size_table.py undesa_pd_2022_hh-size-composition.xlsx \
        input_generator/Global/household_size_undesa2022.csv

Replaces Global/household_number.csv (Appendix A of an unpublished 2022 JRC expert report, no
source or year given; values 0.54-1.27 times UN DESA, Cameroon internally inconsistent, Lesotho
filled with Eswatini's row).
"""
import sys

import pandas as pd

# ISO 3166 alpha-3 -> numeric code used in the UN workbook's "ISO Code" column
M49 = {
    "AGO": 24, "BDI": 108, "BEN": 204, "BFA": 854, "BWA": 72, "CAF": 140, "CIV": 384, "CMR": 120,
    "COD": 180, "COG": 178, "DJI": 262, "ETH": 231, "GAB": 266, "GHA": 288, "GIN": 324, "GMB": 270,
    "GNB": 624, "GNQ": 226, "KEN": 404, "LBR": 430, "LSO": 426, "MDG": 450, "MLI": 466, "MOZ": 508,
    "MRT": 478, "MWI": 454, "NAM": 516, "NER": 562, "NGA": 566, "RWA": 646, "SEN": 686, "SLE": 694,
    "SWZ": 748, "TCD": 148, "TGO": 768, "TZA": 834, "UGA": 800, "ZAF": 710, "ZMB": 894, "ZWE": 716,
}


def build(xlsx_path):
    data = pd.read_excel(xlsx_path, sheet_name="HH size and composition 2022", header=4)
    data = data.rename(columns={
        "Country or area": "country", "ISO Code": "m49", "Data source category": "data_source",
        "Reference date (dd/mm/yyyy)": "date", "Average household size (number of members)": "household_size",
    })
    # the date column mixes real dates and dd/mm/yyyy strings
    data["date"] = pd.to_datetime(data["date"].astype(str), dayfirst=True, errors="coerce", format="mixed")
    data["household_size"] = pd.to_numeric(data["household_size"], errors="coerce")
    data = data.dropna(subset=["household_size", "date"])
    rows = []
    for iso3, code in M49.items():
        country = data[data["m49"] == code].sort_values("date")
        if country.empty:
            raise ValueError(f"{iso3}: no household size in the UN DESA database")
        latest = country.iloc[-1]
        rows.append({"iso3": iso3, "country": latest["country"], "household_size": round(float(latest["household_size"]), 2),
                     "reference_year": latest["date"].year, "data_source": latest["data_source"]})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    table = build(sys.argv[1])
    table.to_csv(sys.argv[2], index=False)
    print(table.to_string(index=False))
