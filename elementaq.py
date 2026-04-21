import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ - Full Suite", layout="wide", page_icon="🧪")

# --- HELPER FUNCTIONS ---
def parse_metadata(name):
    target_match = re.search(r'_(\d+\.?\d*)$', str(name))
    dilution_match = re.search(r'_dil(\d+\.?\d*)', str(name))
    target = float(target_match.group(1)) if target_match else None
    dilution = float(dilution_match.group(1)) if dilution_match else 1.0
    return target, dilution

def calculate_drift_factor(idx, ccv_map, target_val):
    indices = sorted(ccv_map.keys())
    if not indices: return 1.0
    if idx <= indices[0]: return target_val / ccv_map[indices[0]]
    if idx >= indices[-1]: return target_val / ccv_map[indices[-1]]
    for j in range(len(indices) - 1):
        idx_start, idx_end = indices[j], indices[j+1]
        if idx_start <= idx <= idx_end:
            v_start, v_end = ccv_map[idx_start], ccv_map[idx_end]
            interp = v_start + (v_end - v_start) * (idx - idx_start) / (idx_end - idx_start)
            return target_val / interp
    return 1.0

# --- UI HEADER ---
st.title("🧪 ElementaQ: Integrated Analytical Suite")
st.markdown("---")

# --- SIDEBAR SETTINGS ---
st.sidebar.header("Phase 1: RSD Control")
rsd_low = st.sidebar.slider("Yellow Flag (!)", 1.0, 15.0, 6.0)
rsd_high = st.sidebar.slider("Red Flag (!!)", 1.0, 25.0, 10.0)

st.sidebar.header("Phase 2: Metrology")
match_window = st.sidebar.slider("Match Window (%)", 0, 500, (20, 200))
mismatch_action = st.sidebar.selectbox("On Mismatch:", ["Warn only", "Skip Correction"])

# --- FILE UPLOAD ---
uploaded_file = st.file_uploader("Upload Qtegra CSV", type="csv")

if uploaded_file:
    raw_df = pd.read_csv(uploaded_file)
    raw_df.columns = raw_df.columns.str.strip()
    
    # Identify Element columns (usually columns after 'Type')
    elements = [col for col in raw_df.columns if col not in ['Category', 'Label', 'Type']]
    
    # --- PHASE 1: COMPRESSION & RSD FILTERING ---
    st.write("### Step 1: Stability Analysis (RSD Filtering)")
    final_phase1 = []
    total_rows = len(raw_df)
    valid_rows = total_rows - (total_rows % 4)

    for i in range(0, valid_rows, 4):
        block = raw_df.iloc[i : i + 4].copy()
        label = str(block['Label'].iloc[0]).strip()
        stype = str(block['Type'].iloc[0]).strip()
        
        new_row = {'Label': label, 'Type': stype}
        
        for el in elements:
            try:
                avg_val = block[block['Category'].str.contains('average', case=False, na=False)][el].values[0]
                rsd_val = float(block[block['Category'].str.contains('RSD', case=False, na=False)][el].values[0])
                
                # Basic cleaning of the string value
                clean_avg = float(re.sub(r'[^0-9.]', '', str(avg_val))) if '<LQ' not in str(avg_val) else 0.0
                
                res = clean_avg
                if rsd_val > rsd_high: res = f"{clean_avg}!!"
                elif rsd_val > rsd_low: res = f"{clean_avg}!"
                
                if '<LQ' in str(avg_val): res = f"<{clean_avg}"
                
                new_row[el] = res
            except:
                new_row[el] = 0.0
        final_phase1.append(new_row)
    
    ph1_df = pd.DataFrame(final_phase1)
    st.dataframe(ph1_df)

    # --- PHASE 2: DRIFT, BLANK, DILUTION ---
    if st.button("Proceed to Phase 2: Metrological Correction"):
        st.write("### Step 2: Final Metrological Results")
        
        # Parse targets and dilutions from Phase 1 labels
        ph1_df['Target'], ph1_df['Dilution'] = zip(*ph1_df['Label'].map(parse_metadata))
        ph1_df['Row_Idx'] = range(len(ph1_df))
        
        ph2_df = ph1_df.copy()
        
        for el in elements:
            # Prepare CCV Drift Map
            ccv_data = ph1_df[ph1_df['Type'] == 'CCV']
            if ccv_data.empty: continue
            
            # Use numeric conversion for drift mapping
            ccv_map = {}
            for idx, val in zip(ccv_data['Row_Idx'], ccv_data[el]):
                num_val = float(re.sub(r'[^0-9.]', '', str(val)))
                ccv_map[idx] = num_val if num_val > 0 else 1.0 # prevent div zero
            
            target_val = ph1_df[ph1_df['Type'] == 'CCV']['Target'].iloc[0]
            
            # Get Average Blank
            blanks = ph1_df[ph1_df['Type'] == 'BLK'][el].apply(lambda x: float(re.sub(r'[^0-9.]', '', str(x))))
            avg_blank = blanks.mean() if not blanks.empty else 0.0
            
            for i, row in ph2_df.iterrows():
                # Extract numeric value for calculation
                raw_str = str(row[el])
                is_lq = '<' in raw_str
                val = float(re.sub(r'[^0-9.]', '', raw_str))
                
                # Concentration Match check
                is_matched = True
                if target_val and val > 0:
                    ratio = (val / target_val) * 100
                    if not (match_window[0] <= ratio <= match_window[1]):
                        is_matched = False
                
                # Apply Drift
                f_drift = 1.0
                if target_val and (is_matched or mismatch_action == "Warn only"):
                    f_drift = calculate_drift_factor(i, ccv_map, target_val)
                
                # Final calculation: (Raw * Drift - Blank) * Dilution
                final_val = (val * f_drift - avg_blank) * row['Dilution']
                
                # Formatting
                res_str = f"{final_val:.4f}"
                if is_lq: res_str = f"<{final_val:.4f}"
                if not is_matched and not is_lq: res_str += " (!)"
                
                ph2_df.at[i, el] = res_str

        st.success("Metrological correction applied.")
        st.dataframe(ph2_df.drop(columns=['Target', 'Dilution', 'Row_Idx']))
        
        # Download button for Final Report
        csv = ph2_df.to_csv(index=False).encode('utf-8')
        st.download_button("Download Final Report", csv, "ElementaQ_Final_Report.csv", "text/csv")
