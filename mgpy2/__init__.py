"""
mgpy2 — migration package that runs the SSA cluster-parallelization workflow
on the NEW MicroGridsPy engine ("MicroGridsPy Updated", package `core.*`).

Design (see memory: migration-old-to-new-mgpy):
  * demand + solar are produced by the OLD engine's archetype/PVGIS code
    (`microgridspy.utils.archetypes`, `microgridspy.utils.pvgis`) and written
    as NEW-format CSV templates (`load_demand.csv`, `resource_availability.csv`);
  * the NEW engine (`core.multi_year_model.MultiYearModel`) then solves each
    cluster from its `projects/<cat>/inputs/*` files and exports results.

Everything is import-light at module load; heavy engine imports happen inside
functions so this package can be imported for path/config helpers alone.
"""
