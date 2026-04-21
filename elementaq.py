import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ - Audit Trail Edition", layout="wide", page_icon="🧪")

# --- Optimized Helper Functions ---
def parse_metadata(name):
    """Extracts target value and dilution factor from Label with fallback"""
    name_str = str(name)
    # Flexible search for numbers like _10, .10, or just 10 at the end
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
    except ValueError:
        return 0.0

def format_value(val, is_lq=False):
    if is_lq:
        prefix = "<"
        val = max(abs(val), 1e-12)
    else:
        prefix = ""
    if 0 < abs(val) < 1e-6:
        return f"{prefix}{val:.4e}"
    else:
        return f"{prefix}{val:.9f}"

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

# --- Sidebar UI ---
st.sidebar.header("Phase 1: RSD Control")
rsd_low = st.sidebar.slider("Yellow Flag (!)", 1.0, 15.0, 6.0)
rsd_high = st.sidebar.slider("Red Flag (!!)", 1.0, 25.0, 10.0)

st.sidebar.header("Phase 2: Metrology")
match_window = st.sidebar.slider("Match Window (%)", 0, 500, (20, 200))

# --- Application State ---
if 'processed_data' not in st.session_state:
    st.session_state.processed_data = None

# --- Main Interface ---
st.title("🧪 ElementaQ: Analytical Audit Suite")
st.write("Professional ICP Data Engine | v1.3 Stable")
st.markdown("---")

uploaded_file = st.file_uploader("Upload Qtegra CSV File", type="csv")

if uploaded_file:
    raw_df = pd.read_csv(uploaded_file)
    raw_df.columns = raw_df.columns.str.strip()
    elements = [col for col in raw_df.columns if col not in ['Category', 'Label', 'Type']]
    
    # PHASE 1: Compression
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
            val = safe_float(avg_v)
            res = format_value(val, is_lq)
            if not is_lq:
                if rsd_v > rsd_high: res += "!!"
                elif rsd_v > rsd_low: res += "!"
            new_row[el] = res
        final_p1.append(new_row)
    
    ph1_df = pd.DataFrame(final_p1)
    st.write("### 🟢 TABLE 01: Stability & RSD Control")
    st.dataframe(ph1_df)

    # PHASE 2 & 3: Execution
    if st.button("🚀 Run Full Metrological Audit"):
        ph1_df['Target'], ph1_df['Dilution'] = zip(*ph1_df['Label'].map(parse_metadata))
        ph1_df['Row_Idx'] = range(len(ph1_df))
        ph2_df = ph1_df.copy()
        audit_df = ph1_df.copy()

        for el in elements:
            # Smart CCV filter: include rows where Type contains CCV and Label has a numeric target
            ccv_rows = ph1_df[
                (ph1_df['Type'].str.contains('CCV', na=False)) & 
                (~ph1_df[el].astype(str).str.contains('!!')) &
                (ph1_df['Target'].notnull())
            ]
            
            if ccv_rows.empty:
                for i in range(len(ph2_df)): audit_df.at[i, el] = "ERR: CCV NOT FOUND"
                continue

            target_val = ccv_rows['Target'].iloc[0]
            ccv_map = {idx: safe_float(v.split('!')[0]) for idx, v in zip(ccv_rows['Row_Idx'], ccv_rows[el])}

            # Corrected Blank
            corrected_blanks = []
            for idx, row in ph1_df[ph1_df['Type'] == 'BLK'].iterrows():
                val_raw = safe_float(row[el].split('!')[0])
                corrected_blanks.append(val_raw * calculate_drift_factor(idx, ccv_map, target_val))
            avg_blank_corr = np.mean(corrected_blanks) if corrected_blanks else 0.0

            # Metrology Application
            for i, row in ph2_df.iterrows():
                val_raw = safe_float(row[el].split('!')[0])
                is_lq = '<' in str(row[el])
                stype = str(row['Type']).upper()
                drift_f = calculate_drift_factor(i, ccv_map, target_val)
                sub_val = avg_blank_corr if stype in ['S', 'MBB'] else 0.0
                
                final_v = (val_raw * drift_f - sub_val) * row['Dilution']
                ph2_df.at[i, el] = format_value(final_v, is_lq)
                audit_df.at[i, el] = f"D:{drift_f:.3f}|B:{sub_val:.1e}|x{row['Dilution']}"

        st.session_state.processed_data = (ph2_df, audit_df)

    # Persistence of results
    if st.session_state.processed_data:
        res_df, log_df = st.session_state.processed_data
        st.write("### 🔵 TABLE 02: Final Results")
        st.dataframe(res_df.drop(columns=['Target', 'Dilution', 'Row_Idx']))
        
        st.write("### 📜 TABLE 03: Calculation Audit Trail")
        st.dataframe(log_df.drop(columns=['Target', 'Dilution', 'Row_Idx']))

        output = io.StringIO()
        output.write("ELEMENTAQ REPORT v1.3\n\nTABLE 02: RESULTS\n")
        res_df.drop(columns=['Target', 'Dilution', 'Row_Idx']).to_csv(output, index=False)
        output.write("\n\nTABLE 03: AUDIT TRAIL\n")
        log_df.drop(columns=['Target', 'Dilution', 'Row_Idx']).to_csv(output, index=False)
        
        st.download_button("📥 DOWNLOAD AUDIT PACKAGE", output.getvalue(), "ElementaQ_Audit_v13.csv", "text/csv")
