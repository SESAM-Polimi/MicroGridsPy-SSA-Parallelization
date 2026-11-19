# Input data sources

| Table | Source |
| --- | --- |
| `gni.csv` | World Bank, World Development Indicators: `NY.GNP.PCAP.CD` (GNI per capita, Atlas method, current US$) and `NY.GNP.PCAP.PP.KD` (GNI per capita, PPP, constant-price series). Observation year: 2021. Following correction of Djibouti, both columns match the December 2025 and January 2026 WDI archives for all 40 countries. [Atlas](https://data.worldbank.org/indicator/NY.GNP.PCAP.CD); [PPP](https://data.worldbank.org/indicator/NY.GNP.PCAP.PP.KD). |
| `hdi_values.csv` | UNDP, Human Development Report 2025, Statistical Annex, Table 1. HDI observation year: 2023. [Report](https://hdr.undp.org/content/human-development-report-2025). |
| `tabulated_adm1_gini_disp.csv` | Kummu et al., Global subnational Gini coefficient and GNI per capita PPP datasets for 1990–2023, version 3, July 2025. Disposable-income Gini variant. [Dataset](https://doi.org/10.5281/zenodo.16410778). |
| `tabulated_adm1_gini_disp_SSA.csv` | SSA subset of the preceding Kummu Gini table. |
| `polyg_adm1_gini_disp_SSA.gpkg` | SSA regional Gini polygons from the same Kummu dataset; read directly by `income_add.py`. |
| `tables/RWI/{ISO3}_relative_wealth_index.csv` (44 country files) | Chi, Fang, Chatterjee & Blumenstock (2022), Microestimates of wealth for all low- and middle-income countries, PNAS. National CSVs from the April 2021 release. [Paper](https://doi.org/10.1073/pnas.2113658119). |
| `HF_DRC_2025_merged_JRC_GRID3.zip` (included in `tables/`) | DRC healthcare-facility update, 2025, merging JRC and GRID3 records (`HF_merged_JRC_GRID3.shp` and associated files). Provided through the MGPY–JRC shared data folder. Identified by the author as an update of the dataset associated with Moner-Girona et al. (2021), Achieving universal electrification of rural healthcare facilities in sub-Saharan Africa with decentralized renewable energy technologies. [Study](https://doi.org/10.1016/j.joule.2021.09.010). |
