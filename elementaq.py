import pandas as pd
import numpy as np
import streamlit as st
import re

# 1. Extraction Logic
def extract_target_from_type(type_val):
    if pd.isna(type_val): return None
    match = re.search(r'_(\d+\.?\d*)$', str(type_val))
    if match:
        try: return float(match.group(1))
        except: return None
    return None

# 2. Main Processing Engine
def process_icp_data(df, mismatch_limit):
    # Clean column names just in case
    df.columns = [c.strip() for c in df.columns]
    
    if 'Category' not in df.columns:
        st.error("Column 'Category' not found. Please check CSV headers.")
        return None, None, None

    meta_cols = ['Category', 'Label', 'Type']
    element_cols = [c for c in df.columns if c not in meta_cols]
    
    blocks = []
    # Using a safer way to iterate through the 4-row blocks
    for i in range(0, len(df), 4):
        block = df.iloc[i:i+4]
        if len(block) < 4: continue
        
        try:
            avg_row = block[block['Category'].str.contains('average', case=False, na=False)].iloc[0]
            sd_row = block[block['Category'].str.contains('SD', case=False, na=False)].iloc[0]
            rsd_row = block[block['Category'].str.contains('RSD', case=False, na=False)].iloc[0]
            mql_row = block[block['Category'].str.contains('MQL', case=False, na=False)].iloc[0]
            
            blocks.append({
                'index': i,
                'Label': avg_row['Label'],
                'Type': avg_row['Type'],
                'avg': avg_row[element_cols],
                'sd': sd_row[element_cols],
                'rsd': rsd_row[element_cols],
                'mql_inst': mql_row[element_cols]
            })
        except Exception:
            continue

    # --- TABLE 1: Thresholds ---
    t1_list = []
    for b in blocks:
        row = {'Label': b['Label'], 'Type': b['Type']}
        for col in element_cols:
            val = pd.to_numeric(b['avg'][col], errors='coerce')
            mql_m = pd.to_numeric(b['sd'][col], errors='coerce') * 10
            rsd_val = pd.to_numeric(b['rsd'][col], errors='coerce')
            
            if pd.isna(val): formatted = "N/A"
            elif val < mql_m: formatted = f"<{mql_m:.3f}"
            else:
                flag = "!!" if rsd_val > 10 else ("!" if rsd_val > 6 else "")
                formatted = f"{val:.4f}{flag}"
            row[col] = formatted
        t1_list.append(row)
    
    # --- TABLE 2 & 3: Final Math ---
    t2_list = []
    t3_list = []
    
    for col in element_cols:
        inst_mql = pd.to_numeric(blocks[-1]['mql_inst'][col], errors='coerce') if blocks else 0
        blk_vals = [pd.to_numeric(b['avg'][col], errors='coerce') for b in blocks if b['Type'] == 'BLK']
        valid_blks = [v for v in blk_vals if not pd.isna(v) and v > inst_mql]
        avg_blank = np.mean(valid_blks) if valid_blks else 0.0

        ccv_points = []
        for b in blocks:
            if any(k in str(b['Type']) for k in ['CCV', 'ICV']):
                target = extract_target_from_type(b['Type'])
                measured = pd.to_numeric(b['avg'][col], errors='coerce')
                if target and not pd.isna(measured) and measured > (target * 0.5):
                    ccv_points.append({'idx': b['index'], 'f': target/measured, 'target': target})

        for b in blocks:
            if b['Type'] == 'S':
                val_raw = pd.to_numeric(b['avg'][col], errors='coerce')
                f_drift = 1.0
                
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

                if target_ref and not (target_ref * (1 - mismatch_limit/100) <= val_raw <= target_ref * (1 + mismatch_limit/100)):
                    f_drift = 1.0 

                df_factor = 1.0
                if '_dil' in str(b['Label']):
                    try: df_factor = float(str(b['Label']).split('_dil')[-1])
                    except: pass

                res = (val_raw * f_drift - avg_blank) * df_factor
                
                # Update T2/T3
                existing_row = next((d for d in t2_list if d['Label'] == b['Label']), None)
                if not existing_row:
                    t2_row, t3_row = {'Label': b['Label']}, {'Label': b['Label']}
                    t2_list.append(t2_row); t3_list.append(t3_row)
                else:
                    t2_row = existing_row
                    t3_row = next(d for d in t3_list if d['Label'] == b['Label'])

                t2_row[col] = f"{max(0, res):.4f}" if res > 0 else "<LOQ"
                t3_row[col] = f"({val_raw:.3f}*{f_drift:.2f}-{avg_blank:.3f})*{df_factor}"

    return pd.DataFrame(t1_list), pd.DataFrame(t2_list), pd.DataFrame(t3_list)

# --- UI ---
st.set_page_config(layout="wide", page_title="ICP Processor")
st.title("🔬 Rosen Academy: ICP-OES Processor")

# Top Widgets
c1, c2, c3 = st.columns(3)
with c1: drift_val = st.slider("Drift Threshold (%)", 1, 20, 10)
with c2: mismatch_val = st.slider("Mismatch Window (%)", 5, 50, 20)
with c3: st.info("Ready for Data")

# Centered Uploader
st.markdown("---")
uploaded_file = st.file_uploader("Upload Instrument CSV", type="csv")
st.markdown("---")

if uploaded_file:
    df_raw = pd.read_csv(uploaded_file)
    t1, t2, t3 = process_icp_data(df_raw, mismatch_val)
    
    if t1 is not None:
        tabs = st.tabs(["1. Thresholds", "2. Results", "3. Math Log"])
        tabs[0].dataframe(t1, use_container_width=True)
        tabs[1].dataframe(t2, use_container_width=True)
        tabs[2].dataframe(t3, use_container_width=True)
