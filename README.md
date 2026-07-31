# MicroGridsPy-SSA-Parallelization\
\
This repository contains the scripts developed to parallelize large-scale MicroGridsPy simulations for Sub-Saharan Africa (SSA). The workflow supports both local execution and High Performance Computing (HPC) environments.\
\
## Input generation\
\
- **advanced_sample_generator_v2.py** \'96 Generates the simulation samples.\
- **fillerV5.py** \'96 Prepares and completes the input datasets required by MicroGridsPy.\
- **sparse_connectivity.py** \'96 Computes sparse cluster connectivity information.\
\
## Local workflow\
\
- **orchestrator.py** \'96 Coordinates and manages the execution of parallel simulations on a local machine.\
- **run_nostreamlit_update.py** \'96 Executes MicroGridsPy without the Streamlit interface for local parallel runs.\
\
## HPC workflow\
\
- **submit_jobs.sh** \'96 Submits multiple jobs to the HPC scheduler.\
- **submit_array.sh** \'96 Submits simulations as an HPC array job.\
- **run_single_cluster.py** \'96 Executes a single cluster simulation on an HPC compute node.\
- **run_single_cluster_debug.py** \'96 Debug version of the HPC single-cluster execution.\
- **run_nostreamlit_update.py** \'96 Executes MicroGridsPy without the Streamlit interface within the HPC workflow.\
\
## Post-processing\
\
- **postprocess.py** \'96 Aggregates and processes simulation outputs.\
\
## Purpose\
\
The workflow enables the execution of thousands of independent MicroGridsPy simulations, either on a local workstation or on High Performance Computing (HPC) systems, significantly reducing the computational time required for continental-scale electrification analyses.