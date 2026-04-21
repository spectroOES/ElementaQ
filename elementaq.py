import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ - Selective Drift Edition", layout="wide", page_icon="🧪")

# --- Core Functions ---
def parse_metadata(name):
    name_str = str(name)
    target_match = re.search(r'[_.]?(\d+\.?\d*)$', name_str)
    dilution_match = re.search(r'_dil(\d+\.?\d*)', name_str)
    target = float(target_match.group(1)) if target_match else None
    dilution = float(dilution_match.group(1)) if dilution_match else 1.0
    return target, dilution

def safe_float(val_str):
    if pd.isna(val_str) or val_str == "": return 0.0
    clean_val = re.sub(r'[^0-9.eE-]', '', str(val_str).split('<')[0])
    try:
        return float(clean_val) if clean_val else 0.0
    except ValueError: return 0.0

def format_value(val, is_lq=False):
    if is_lq:
        prefix = "<"
        val = max(abs(val), 1e-12)
    else: prefix = ""
    if 0 < abs(val) < 1e-6: return f"{prefix}{val:.4e}"
    else: return f"{prefix}{val:.9f}"

def calculate_drift_factor(idx, ccv_map, target_val):
    indices = sorted(ccv_map.keys())
    if not indices: return 1.0
    if len(indices) == 1: return target_val / ccv_map[indices[0]]
    if idx <= indices[0]: return target_val / ccv_map[indices[0]]
    if idx >= indices[-1]: return target_val / ccv_map[indices[-1]]
    for j in range(len(indices) - 1):
        idx_start, idx_end = indices[j], indices[j+1]
        if idx_start <= idx <= idx_end:
            v_start, v_end = ccv_map[idx_start], ccv_map[idx_end]
            interp_res = v_start + (v_end - v_start) * (idx - idx_start) / (idx_end - idx_start)
            return target_val / interp_res
    return 1.0

# --- Sidebar ---
st.sidebar.header("Phase 1: RSD Control")
rsd_low = st.sidebar.slider("Yellow Flag (!)", 1.0, 15.0, 6.0)
rsd_high = st.sidebar.slider("Red Flag (!!)", 1.0, 25.0, 10.0)

# --- Session State ---
if 'ph1_done' not in st.session_state: st.session_state.ph1_done = None
if 'ph2_done' not in st.session_state: st.session_state.ph2_done = None

st.title("🧪 ElementaQ: Selective Metrology Suite")
st.write("Drift Correction: BLK & S Only | v1.4")
st.markdown("---")

uploaded_file = st.file_uploader("Upload Qtegra CSV File", type="csv")

if uploaded_file:
    raw_df = pd.read_csv(uploaded_file)
    raw_df.columns = raw_df.columns.str.strip()
    elements = [col for col in raw_df.columns if col not in ['Category', 'Label', 'Type']]
    
    # --- BUTTON 1: STEP 01 ---
    if st.button("📊 Run Phase 1: RSD & Compression"):
        final_p1 = []
        valid_rows = len(raw_df) - (len(raw_df) % 4)
        for i in range(0, valid_rows, 4):
            block = raw_df.iloc[i : i + 4].copy()
            label, stype = str(block['Label'].iloc[0]).strip(), str(block['Type'].iloc[0]).strip().upper()
            new_row = {'Label': label, 'Type': stype}
            for el in elements:
                avg_v = block[block['Category'].str.contains('average', case=False, na=False)][el].values[0]
                rsd_v = safe_float(block[block['Category'].str.contains('RSD', case=False, na=False)][el].values[0])
                is_lq = '<LQ' in str(avg_v)
                res = format_value(safe_float(avg_v), is_lq)
                if not is_lq:
                    if rsd_v > rsd_high: res += "!!"
                    elif rsd_v > rsd_low: res += "!"
                new_row[el] = res
            final_p1.append(new_row)
        st.session_state.ph1_done = pd.DataFrame(final_p1)

    if st.session_state.ph1_done is not None:
        st.write("### 🟢 TABLE 01: RSD Stability Report")
        st.dataframe(st.session_state.ph1_done)

        # --- BUTTON 2: STEP 02 ---
        if st.button("🚀 Run Phase 2: Selective Drift & Blank Subtraction"):
            ph1 = st.session_state.ph1_done.copy()
            ph1['Target'], ph1['Dilution'] = zip(*ph1['Label'].map(parse_metadata))
            ph1['Row_Idx'] = range(len(ph1))
            
            ph2_df, audit_df = ph1.copy(), ph1.copy()

            for el in elements:
                ccv_rows = ph1[(ph1['Type'].str.contains('CCV')) & (~ph1[el].astype(str).str.contains('!!')) & (ph1['Target'].notnull())]
                if ccv_rows.empty:
                    for i in range(len(ph2_df)): audit_df.at[i, el] = "ERR: NO CCV"
                    continue

                target_v = ccv_rows['Target'].iloc[0]
                ccv_map = {idx: safe_float(v.split('!')[0]) for idx, v in zip(ccv_rows['Row_Idx'], ccv_rows[el])}
                
                # Corrected Blank calculation
                blanks = [safe_float(r[el].split('!')[0]) * calculate_drift_factor(idx, ccv_map, target_v) 
                          for idx, r in ph1[ph1['Type'] == 'BLK'].iterrows()]
                avg_b = np.mean(blanks) if blanks else 0.0

                for i, row in ph2_df.iterrows():
                    val_raw = safe_float(row[el].split('!')[0])
                    stype = str(row['Type']).upper()
                    
                    # Logic: ONLY S and BLK get drift correction
                    d_factor = calculate_drift_factor(i, ccv_map, target_v) if stype in ['S', 'BLK'] else 1.0
                    sub_val = avg_b if stype in ['S', 'MBB'] else 0.0
                    
                    final_v = (val_raw * d_factor - sub_val) * row['Dilution']
                    ph2_df.at[i, el] = format_value(final_v, '<' in str(row[el]))
                    audit_df.at[i, el] = f"Drift:{d_factor:.3f}|B-Sub:{sub_val:.1e}|x{row['Dilution']}"

            st.session_state.ph2_done = (ph2_df, audit_df)

    if st.session_state.ph2_done:
        res, log = st.session_state.ph2_done
        st.write("### 🔵 TABLE 02: Metrologically Corrected Results")
        st.dataframe(res.drop(columns=['Target', 'Dilution', 'Row_Idx']))
        st.write("### 📜 TABLE 03: Calculation Audit Trail")
        st.dataframe(log.drop(columns=['Target', 'Dilution', 'Row_Idx']))

        out = io.StringIO()
        out.write("ELEMENTAQ v1.4 REPORT\n\nTABLE 02: RESULTS\n")
        res.drop(columns=['Target', 'Dilution', 'Row_Idx']).to_csv(out, index=False)
        out.write("\n\nTABLE 03: AUDIT TRAIL\n")
        log.drop(columns=['Target', 'Dilution', 'Row_Idx']).to_csv(out, index=False)
        st.download_button("📥 DOWNLOAD REPORT", out.getvalue(), "ElementaQ_v14.csv", "text/csv")
