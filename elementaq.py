import pandas as pd
import numpy as np
import streamlit as st
import re

def extract_target_from_type(type_val):
    """
    Extracts numerical value from the 'Type' column (e.g., 'CCV_0.1' -> 0.1).
    Label is ignored as it is considered an arbitrary name.
    """
    # Search for a number following an underscore at the end of the Type string
    match = re.search(r'_(\d+\.?\d*)$', str(type_val))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None

def process_icp_v3_eng(df, drift_limit, mismatch_limit):
    # 1. Structural Setup
    # Element columns are everything following 'Type'
    meta_cols = ['Category', 'Label', 'Type']
    element_cols = [c for c in df.columns if c not in meta_cols]
    
    # Grouping by 4 rows (1 sample block: Avg, SD, RSD, MQL)
    blocks = []
    for i in range(0, len(df), 4):
        block = df.iloc[i:i+4]
        if block.empty or len(block) < 4:
            continue
        
        avg_row = block[block['Category'] == 'Concentration average'].iloc[0]
        sd_row = block[block['Category'] == 'Concentration SD'].iloc[0]
        rsd_row = block[block['Category'] == 'Concentration RSD'].iloc[0]
        mql_row = block[block['Category'] == 'MQL'].iloc[0]
        
        blocks.append({
            'index': i,
            'Label': avg_row['Label'],
            'Type': avg_row['Type'],
            'avg': avg_row[element_cols],
            'sd': sd_row[element_cols],
            'rsd': rsd_row[element_cols],
            'mql_inst': mql_row[element_cols]
        })

    # --- STAGE 1: TABLE 1 (MQL & RSD VALIDATION) ---
    t1_data = []
    for b in blocks:
        row = {'Label': b['Label'], 'Type': b['Type']}
        for col in element_cols:
            val = b['avg'][col]
            mql_m = b['sd'][col] * 10  # Matrix MQL logic
            
            if pd.isna(val):
                formatted = "N/A"
            elif val < mql_m:
                formatted = f"<{mql_m:.3f}"
            else:
                flag = "!!" if b['rsd'][col] > 10 else ("!" if b['rsd'][col] > 6 else "")
                formatted = f"{val:.4f}{flag}"
            row[col] = formatted
        t1_data.append(row)
    table1 = pd.DataFrame(t1_data)

    # --- STAGE 2: CALCULATION (TABLE 2 & LOGS) ---
    t2_results = []
    t3_logs = []
    
    for col in element_cols:
        # A) Analytical Blank Calculation
        # Based on v3.0: average of BLKs where value > Instrument MQL
        blk_vals = [b['avg'][col] for b in blocks if b['Type'] == 'BLK']
        # Reference Instrument MQL from the last block for this element
        inst_mql = blocks[-1]['mql_inst'][col] if blocks else 0.0
        valid_blks = [v for v in blk_vals if v > inst_mql]
        avg_blank = np.mean(valid_blks) if valid_blks else 0.0

        # B) Identify CCV/ICV Drift Points
        ccv_points = []
        for b in blocks:
            if any(key in str(b['Type']) for key in ['CCV', 'ICV']):
                target = extract_target_from_type(b['Type'])
                measured = b['avg'][col]
                # Validation window for inclusion: measured must be > 50% of target
                if target and measured > (target * 0.5):
                    ccv_points.append({'idx': b['index'], 'f': target/measured, 'target': target})

        # C) Process Samples (S)
        for b in blocks:
            if b['Type'] == 'S':
                val_raw = b['avg'][col]
                f_drift = 1.0
                
                # Linear Interpolation of Drift Factor
                before = [p for p in ccv_points if p['idx'] <= b['index']]
                after = [p for p in ccv_points if p['idx'] > b['index']]
                
                target_ref = None
                if before and after:
                    p1, p2 = before[-1], after[0]
                    # Interpolation based on sequence position
                    f_drift = p1['f'] + (p2['f'] - p1['f']) * (b['index'] - p1['idx']) / (p2['idx'] - p1['idx'])
                    target_ref = p1['target']
                elif before:
                    f_drift = before[-1]['f']
                    target_ref = before[-1]['target']

                # Confidence Window (80-120%): Skip correction if outside range
                if target_ref:
                    if not (target_ref * 0.8 <= val_raw <= target_ref * 1.2):
                        f_drift = 1.0 # Noise shouldn't be corrected by drift
                
                # Dilution Factor parsing (S_dilXX)
                df_factor = 1.0
                if '_dil' in str(b['Label']):
                    try:
                        df_factor = float(str(b['Label']).split('_dil')[-1])
                    except:
                        pass

                # Master Equation: (Raw * f - Blank) * DF
                res = (val_raw * f_drift - avg_blank) * df_factor
                
                # Build Row if not exists
                if not any(d['Label'] == b['Label'] for d in t2_results):
                    t2_results.append({'Label': b['Label']})
                    t3_logs.append({'Label': b['Label']})
                
                t2_results[-1][col] = f"{max(0, res):.4f}" if res > 0 else "<LOQ"
                t3_logs[-1][col] = f"({val_raw:.3f}*{f_drift:.2f}-{avg_blank:.3f})*{df_factor}"

    return table1, pd.DataFrame(t2_results), pd.DataFrame(t3_logs)

# --- Streamlit Interface ---
st.set_page_config(layout="wide", page_title="Rosen Academy ICP Processor")
st.title("🔬 ICP-OES Processor v3.0")

with st.sidebar:
    st.header("Settings")
    drift_val = st.slider("Max Drift Alert (%)", 5, 20, 10)
    mismatch_val = st.slider("Confidence Window (%)", 10, 50, 20)
    uploaded_file = st.file_uploader("Upload Instrument CSV", type="csv")

if uploaded_file:
    # Read and clean
    raw_df = pd.read_csv(uploaded_file)
    raw_df = raw_df.dropna(subset=['Category'])
    
    # Processing
    t1, t2, t3 = process_icp_v3_eng(raw_df, drift_val, mismatch_val)
    
    # UI Tabs
    tab1, tab2, tab3 = st.tabs(["Table 1: MQL & RSD", "Table 2: Final Results", "Table 3: Math Audit Log"])
    
    with tab1:
        st.subheader("Raw Data with Matrix Thresholds")
        st.dataframe(t1)
    
    with tab2:
        st.subheader("Corrected Concentrations (Blank, Drift, Dilution)")
        st.dataframe(t2)
        
    with tab3:
        st.subheader("Calculation Traceability")
        st.info("Formula format: (Measured * DriftFactor - Blank) * DilutionFactor")
        st.dataframe(t3)
