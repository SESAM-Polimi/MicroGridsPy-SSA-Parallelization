import pandas as pd
import os
import re
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
import random


# inputs
SOURCE_CSV = Path('/Users/matteo/Desktop/Tesi/Countries input/AGO/AGO_Pop_clusters_2025.csv')
HOUSEHOLD_CSV = Path('/Users/matteo/Desktop/Tesi/Data sheet/household_number.csv')
SAMPLE_CSV_INPUT = Path("/Users/matteo/Desktop/Tesi/Data sheet/sample.csv")
SAMPLE_CSV_OUTPUT = Path('/Users/matteo/Desktop/sample.csv')
HDI_CSV = Path("/Users/matteo/Desktop/Tesi/Data sheet/hdi_values.csv")  

COLUMN_MAPPING = {
    "cat": "cat",
    "health_first": "hospital_1",
    "health_primary": "hospital_2",
    "health_secondary": "hospital_3",
    "health_tertiary": "hospital_5",
    "x": "lon",
    "y": "lat",
    "distance_grid_km": "distance_grid_km",
    "cl_type": "cooling",
    "pop_sum": "pop_sum",
    "density": "density",
    "num_schools": "schools",
    "school_total_demand": "school_total_demand",
    "rwi_real": "rwi",
    "rwi_std": "rwi_std"
}

COOLING_MAPPING = {
    1: "NC",
    2: "AS", 
    3: "OM",
    4: "AY"
}

PLOT_EXAMPLES = True
N_PLOT = 5


def load_hdi_adjustments(hdi_csv_path):
    """
    Load HDI values and create bonus/malus adjustments scaled from -1 to +1.
    Lower HDI = bigger malus (-1), Higher HDI = bigger bonus (+1)
    """
    try:
        hdi_df = pd.read_csv(hdi_csv_path)
        # Assume first column is country code, second is HDI value
        hdi_df.columns = ['country_code', 'hdi_value']
        
        # Scale HDI values to -1 to +1 range
        min_hdi = hdi_df['hdi_value'].min()
        max_hdi = hdi_df['hdi_value'].max()
        
        # Linear scaling: lower HDI gets -1 (malus), higher HDI gets +1 (bonus)
        hdi_df['hdi_adjustment'] = 2 * (hdi_df['hdi_value'] - min_hdi) / (max_hdi - min_hdi) - 1
        
        print(f"HDI Adjustments loaded:")
        print(f"  HDI range: {min_hdi:.3f} to {max_hdi:.3f}")
        print(f"  Adjustment range: {hdi_df['hdi_adjustment'].min():.3f} to {hdi_df['hdi_adjustment'].max():.3f}")
        
        # Convert to dictionary for easy lookup
        return dict(zip(hdi_df['country_code'], hdi_df['hdi_adjustment']))
        
    except Exception as e:
        print(f"Warning: Could not load HDI file {hdi_csv_path}: {e}")
        print("Using default adjustment of 0.0")
        return {}


def allocate_households_hdi_adjusted(rwi_value, rwi_std, total_households, hdi_adjustment=0.0, plot=False):
    """
    Allocate households across 5 tiers based on RWI with HDI adjustment.
    
    Args:
        rwi_value: Original RWI value
        rwi_std: RWI standard deviation
        total_households: Total number of households to allocate
        hdi_adjustment: HDI-based bonus/malus (-1 to +1)
        plot: Whether to show the plot
    """
    # Apply HDI adjustment to RWI center
    original_center = rwi_value
    adjusted_center = rwi_value + hdi_adjustment
    
    # Use minimum std to avoid extremely narrow distributions
    std = max(rwi_std, 0.1)
    
    # RWI configuration: -2.5 to 2.5 range
    bins = [-2.5, -1.5, -0.5, 0.5, 1.5, 2.5]
    x = np.linspace(-3.0, 3.0, 1000)
    
    # Generate Gaussian PDFs
    # Original distribution (red dotted line)
    pdf_original = np.exp(-0.5 * ((x - original_center)/std)**2)
    pdf_original /= pdf_original.sum()
    
    # HDI-adjusted distribution (green line)
    pdf_adjusted = np.exp(-0.5 * ((x - adjusted_center)/std)**2)
    pdf_adjusted /= pdf_adjusted.sum()
    
    # Calculate probability for each bin using adjusted distribution
    counts = []
    for i in range(5):
        idx = (x >= bins[i]) & (x < bins[i+1])
        fraction = pdf_adjusted[idx].sum()
        counts.append(fraction * total_households)
    
    if plot:
        plt.figure(figsize=(15, 6))
        
        # Plot 1: Both RWI distributions
        plt.subplot(1, 3, 1)
        plt.plot(x, pdf_original * total_households, 'r--', label=f'Original RWI (center={original_center:.2f})', linewidth=2)
        plt.plot(x, pdf_adjusted * total_households, 'g-', label=f'HDI-Adjusted RWI (center={adjusted_center:.2f})', linewidth=2)
        plt.axvline(original_center, color='red', linestyle=':', alpha=0.7, label='Original Center')
        plt.axvline(adjusted_center, color='green', linestyle=':', alpha=0.7, label='Adjusted Center')
        
        # Show bin boundaries
        for i, bin_edge in enumerate(bins[:-1]):
            plt.axvline(bin_edge, color='gray', linestyle='-', alpha=0.3)
        
        plt.xlabel('RWI Value')
        plt.ylabel('Households')
        plt.title(f'RWI Distributions (HDI adj={hdi_adjustment:+.3f})')
        plt.legend(loc='center left', bbox_to_anchor=(1, 0.5))
        plt.grid(True, alpha=0.3)
        
        # Plot 2: Household allocation by tier
        plt.subplot(1, 3, 2)
        plt.bar(range(1, 6), counts, alpha=0.7, color='green')
        plt.xlabel('Tier (1=highest deprivation, 5=lowest)')
        plt.ylabel('Households')
        plt.title(f'Household Allocation (total={total_households:.0f})')
        plt.xticks(range(1, 6))
        plt.grid(True, alpha=0.3)
        
        # Plot 3: HDI Adjustment Impact
        plt.subplot(1, 3, 3)
        plt.bar(['Original', 'HDI-Adjusted'], [original_center, adjusted_center], 
                color=['red', 'green'], alpha=0.7)
        plt.ylabel('RWI Center Value')
        plt.title('HDI Adjustment Impact')
        plt.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.show()
    
    return counts


def main(source_csv, household_csv, sample_csv_input, sample_csv_output, hdi_csv, column_mapping):
    # Load HDI adjustments
    hdi_adjustments = load_hdi_adjustments(hdi_csv)
    
    # eliminate old output file
    if sample_csv_output.exists():
        sample_csv_output.unlink()

    # load source
    source_df = pd.read_csv(source_csv)

    # load or create sample from input template
    if sample_csv_input.exists():
        sample_df = pd.read_csv(sample_csv_input)
    else:
        sample_df = pd.DataFrame(columns=[
            "cat","h_tier1","h_tier2","h_tier3","h_tier4","h_tier5",
            "hospital_1","hospital_2","hospital_3","hospital_4","hospital_5",
            "schools","school_total_demand","lat","lon","distance_grid_km","pop_sum","density","cooling","rwi","rwi_std"
        ])

    # adjust row number
    max_rows = max(
        len(sample_df),
        max(len(source_df[src]) for src in column_mapping.keys() if src in source_df.columns)
    )
    if len(sample_df) < max_rows:
        new_rows = pd.DataFrame({
            col: [0 if col not in ["cooling"] else ""] * (max_rows - len(sample_df))
            for col in sample_df.columns
        })
        sample_df = pd.concat([sample_df, new_rows], ignore_index=True)

    # reset values
    for col in sample_df.columns:
        if col != "cooling":
            sample_df.loc[:, col] = 0
        else:
            sample_df.loc[:, col] = ""

    # copy source data → sample
    for source_col, sample_col in column_mapping.items():
        if source_col not in source_df.columns:
            print(f"no column '{source_col}' in source file")
            continue
        if sample_col not in sample_df.columns:
            print(f"no column '{sample_col}' in sample file")
            continue
        
        data_to_copy = source_df[source_col].values
        # cooling string mapping
        if sample_col == "cooling":
            mapped_data = []
            for val in data_to_copy:
                if pd.isna(val) or val == "":
                    mapped_data.append("")
                else:
                    try:
                        numeric_val = int(float(val))
                        mapped_data.append(COOLING_MAPPING.get(numeric_val, ""))
                    except (ValueError, TypeError):
                        mapped_data.append("")
            sample_df.loc[:len(mapped_data)-1, sample_col] = mapped_data
        else:
            sample_df.loc[:len(data_to_copy)-1, sample_col] = data_to_copy

    # cast cat to integer
    if 'cat' in sample_df.columns:
        sample_df['cat'] = sample_df['cat'].astype(pd.Int64Dtype())

    # household calculation
    country_code_match = re.match(r"([A-Z]{3})_", source_csv.name)
    if country_code_match:
        country_code = country_code_match.group(1)
        
        # Get HDI adjustment for this country
        hdi_adjustment = hdi_adjustments.get(country_code, 0.0)
        print(f"Country: {country_code}, HDI Adjustment: {hdi_adjustment:+.3f}")
        
        hh_df = pd.read_csv(household_csv)
        mean_household = hh_df.loc[
            hh_df['Country Code'] == country_code, 'Mean national'
        ].values
        if len(mean_household) == 0:
            print(f"Country code {country_code} not found in household_number.csv")
        else:
            mean_household = float(mean_household[0])
            
            # pick random rows for plotting
            if PLOT_EXAMPLES:
                plot_rows = random.sample(range(len(source_df)), min(N_PLOT, len(source_df)))
                # Also add category 241 for plotting if it exists
                cat_241_rows = source_df[source_df['cat'] == 241].index.tolist()
                if cat_241_rows:
                    plot_rows.append(cat_241_rows[0])  # Add first occurrence of cat 241
                    print(f"Added category 241 (row {cat_241_rows[0]}) to plotting list")
            else:
                plot_rows = []

            print(f"Using RWI with HDI adjustment")

            for idx, row in source_df.iterrows():
                # Use RWI only
                if pd.isnull(row.get('pop_sum')) or pd.isnull(row.get('rwi_real')) or pd.isnull(row.get('cat')) or pd.isnull(row.get('rwi_std')):
                    continue
                
                rwi_value = float(row['rwi_real'])
                rwi_std = float(row['rwi_std'])
                n_households = float(row['pop_sum']) / mean_household
                cat_val = row['cat']

                # allocate households across tiers using HDI-adjusted RWI
                allocations = allocate_households_hdi_adjusted(
                    rwi_value, rwi_std, n_households, hdi_adjustment, plot=(idx in plot_rows)
                )

                match = sample_df['cat'] == cat_val
                if match.any():
                    for i in range(5):
                        sample_df.loc[match, f"h_tier{i+1}"] = int(round(allocations[i]))

                    # copy hospitals from source
                    for h_idx, h_col in enumerate(["health_first","health_primary","health_secondary","health_tertiary"], start=2):
                        if h_col in source_df.columns:
                            sample_df.loc[match, f"hospital_{h_idx}"] = row[h_col]
    else:
        print("Could not extract country code from source file name.")

    # save
    sample_df.to_csv(sample_csv_output, index=False)
    print(f"done in {sample_csv_output}")


if __name__ == "__main__":
    main(SOURCE_CSV, HOUSEHOLD_CSV, SAMPLE_CSV_INPUT, SAMPLE_CSV_OUTPUT, HDI_CSV, COLUMN_MAPPING)