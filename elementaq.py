import pandas as pd
import numpy as np
import streamlit as st
import re

def extract_target_from_type(type_val):
    """
    Extracts numerical value strictly from the 'Type' column.
    Pattern: CCV_10 or ICV_0.5 -> returns 10 or 0.5.
    """
    if pd.isna(type_val):
        return None
    match = re.search(r'_(\d+\.?\d*)$', str(type_val))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None

def process_icp_data(df, drift_limit, mismatch_limit):
    # 1. Column Identification
    meta_cols = ['Category', 'Label', 'Type']
    element_cols = [c for c in df.columns if c not in meta_cols]
    
    # Grouping by 4 rows (Avg, SD, RSD, MQL)
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

    # --- STAGE 1: TABLE 1 (Thresholds) ---
    t1_data = []
    for b in blocks:
        row = {'Label': b['Label'], 'Type': b['Type']}
        for col in element_cols:
            val = b['avg'][col]
            mql_matrix = b['sd'][col] * 10 
            
            if pd.isna(val):
                formatted = "N/A"
            elif val < mql_matrix:
                formatted = f"<{mql_matrix:.3f}"
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
        blk_vals = [b['avg'][col] for b in blocks if b['Type'] == 'BLK']
        inst_mql = blocks[-1]['mql_inst'][col] if blocks else 0.0
        valid_blks = [v for v in blk_vals if v > inst_mql]
        avg_blank = np.mean(valid_blks) if valid_blks else 0.0

        # B) Drift Correction Points (from Type only)
        ccv_points = []
        for b in blocks:
            if any(k in str(b['Type']) for k in ['CCV', 'ICV']):
                target = extract_target_from_type(b['Type'])
                measured = b['avg'][col]
                if target and measured > (target * 0.5):
                    ccv_points.append({'idx': b['index'], 'f': target/measured, 'target': target})

        # C) Sample Processing
        for b in blocks:
            if b['Type'] == 'S':
                val_raw = b['avg'][col]
                f_drift = 1.0
                
                # Linear Interpolation
                before = [p for p in ccv_points if p['idx'] <= b['index']]
                after = [p for p in ccv_points if p['idx'] > b['index']]
                
                target_ref = None
                if before and after:
                    p1, p2 = before[-1], after[0]
                    f_drift = p1['f'] + (p2['f'] - p1['f']) * (b['index'] - p1['idx']) / (p2['idx'] - p1['idx'])
                    target_ref = p1['target']
                elif before:
                    f_drift = before[-1]['f']
                    target_ref = before[-1]['target']

                # Mismatch Rule (80-120%)
                if target_ref:
                    if not (target_ref * (1 - mismatch_limit/100) <= val_raw <= target_ref * (1 + mismatch_limit/100)):
                        f_drift = 1.0 
                
                # Dilution from Label suffix
                df_factor = 1.0
                if '_dil' in str(b['Label']):
                    try:
                        df_factor = float(str(b['Label']).split('_dil')[-1])
                    except:
                        pass

                # Calculation
                res = (val_raw * f_drift - avg_blank) * df_factor
                
                if not any(d['Label'] == b['Label'] for d in t2_results):
                    t2_results.append({'Label': b['Label']})
                    t3_logs.append({'Label': b['Label']})
                
                t2_results[-1][col] = f"{max(0, res):.4f}" if res > 0 else "<LOQ"
                t3_logs[-1][col] = f"({val_raw:.3f}*{f_drift:.2f}-{avg_blank:.3f})*{df_factor}"

    return table1, pd.DataFrame(t2_results), pd.DataFrame(t3_logs)

# --- UI LAYOUT ---
st.set_page_config(layout="wide", page_title="ICP Data Processor")
st.title("🔬 Rosen Academy: ICP-OES Data Processor")

# Top Row Widgets
col1, col2, col3 = st.columns(3)
with col1:
    drift_val = st.slider("Max Drift (%)", 1, 20, 10)
with col2:
    mismatch_val = st.slider("Confidence Window (%)", 5, 50, 20)
with col3:
    st.write("") # Spacer
    st.info("Logic v3.0 Active")

# Central File Upload
st.markdown("---")
uploaded_file = st.file_uploader("Step 1: Upload Instrument CSV File", type="csv")
st.markdown("---")

if uploaded_file:
    raw_df = pd.read_csv(uploaded_file).dropna(subset=['Category'])
    t1, t2, t3 = process_icp_data(raw_df, drift_val, mismatch_val)
    
    tabs = st.tabs(["Table 1: Thresholds", "Table 2: Final Results", "Table 3: Math Log"])
    
    with tabs[0]:
        st.dataframe(t1, use_container_width=True)
    with tabs[1]:
        st.dataframe(t2, use_container_width=True)
    with tabs[2]:
        st.dataframe(t3, use_container_width=True)
