import streamlit as st
import pandas as pd
import numpy as np
import re

st.set_page_config(page_title="ElementaQ", layout="wide")

# --- CORE FUNCTIONS ---
def parse_metadata(name):
    """Extracts Target concentration and Dilution factor from Name."""
    target_match = re.search(r'_(\d+\.?\d*)$', str(name))
    dilution_match = re.search(r'_dil(\d+\.?\d*)', str(name))
    
    target = float(target_match.group(1)) if target_match else None
    dilution = float(dilution_match.group(1)) if dilution_match else 1.0
    return target, dilution

def calculate_drift_factor(idx, ccv_map, target_val):
    """Calculates linear interpolation factor between two CCV points."""
    indices = sorted(ccv_map.keys())
    if not indices:
        return 1.0
    
    # If before first CCV or after last CCV, use the nearest one
    if idx <= indices[0]:
        return target_val / ccv_map[indices[0]]
    if idx >= indices[-1]:
        return target_val / ccv_map[indices[-1]]
    
    # Linear interpolation between two CCVs
    for j in range(len(indices) - 1):
        idx_start, idx_end = indices[j], indices[j+1]
        if idx_start <= idx <= idx_end:
            val_start, val_end = ccv_map[idx_start], ccv_map[idx_end]
            # Interpolated measured value at position idx
            interp_meas = val_start + (val_end - val_start) * (idx - idx_start) / (idx_end - idx_start)
            return target_val / interp_meas
    return 1.0

# --- UI SETUP ---
st.title("🧪 ElementaQ")
st.sidebar.header("Processing Settings")

rsd_limit = st.sidebar.slider("RSD Threshold (%)", 0.0, 25.0, 10.0)
drift_fail = st.sidebar.slider("CCV Fail Limit (±%)", 5, 20, 10) / 100.0
match_window = st.sidebar.slider("Match Window (%)", 0, 500, (20, 200))
mismatch_action = st.sidebar.selectbox("On Conc. Mismatch:", ["Warn only", "Skip Correction"])

uploaded_file = st.file_uploader("Upload Qtegra CSV", type="csv")

if uploaded_file:
    df = pd.read_csv(uploaded_file)
    
    # Data Cleaning: Identify numeric columns (elements)
    element_cols = [c for c in df.columns if any(char.isdigit() for char in c) or 'Conc' in c]
    
    if st.button("Run Full Analysis"):
        st.write("### Phase 1: RSD & Mean Calculation")
        # Logic for Phase 1 would go here (already implemented in your lab)
        # Assuming df is already averaged for this demonstration
        
        st.write("### Phase 2: Drift, Blank, and Dilution")
        
        # 1. Parse Metadata
        df['Target'], df['Dilution'] = zip(*df['Name'].map(parse_metadata))
        df['Row_Idx'] = range(len(df))
        
        final_results = df.copy()
        
        for element in element_cols:
            # Map CCV positions and values for this specific element
            ccv_data = df[(df['Type'] == 'CCV') & (df['Target'].notnull())]
            ccv_map = dict(zip(ccv_data['Row_Idx'], ccv_data[element]))
            
            # Get common target (assuming one target per run for simplicity)
            target_val = ccv_data['Target'].iloc[0] if not ccv_data.empty else None
            
            # Get Blanks
            blank_val = df[df['Type'] == 'BLK'][element].mean() if not df[df['Type'] == 'BLK'].empty else 0
            
            for i, row in df.iterrows():
                val = row[element]
                
                # Check Concentration Match (Window of Trust)
                is_matched = True
                if target_val and val > 0:
                    ratio = (val / target_val) * 100
                    if not (match_window[0] <= ratio <= match_window[1]):
                        is_matched = False
                
                # Apply Drift Correction
                f_drift = 1.0
                if target_val and (is_matched or mismatch_action == "Warn only"):
                    f_drift = calculate_drift_factor(i, ccv_map, target_val)
                
                # Final calculation: (Raw * Drift - Blank) * Dilution
                corrected = (val * f_drift - blank_val) * row['Dilution']
                
                # Update Final Table
                final_results.at[i, element] = corrected
                if not is_matched:
                    final_results.at[i, element] = f"{corrected} (!)"

        st.success("Analysis Complete")
        st.write("#### Final Corrected Data")
        st.dataframe(final_results)
        
        csv = final_results.to_csv(index=False).encode('utf-8')
        st.download_button("Download Result", csv, "elementaq_results.csv", "text/csv")
