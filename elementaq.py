import streamlit as st
import pandas as pd
import numpy as np
import re
import io

# 1. Page Configuration
st.set_page_config(page_title="ElementaQ v2.6", layout="wide", page_icon="🧪")

# 2. FIXED SIDEBAR WIDGETS
# These are placed outside any logic to ensure they never disappear.
st.sidebar.header("Global Control Settings")
rsd_limit = st.sidebar.slider("RSD Threshold (%)", 1.0, 25.0, 10.0)
drift_threshold = st.sidebar.slider("CCV Drift Limit (%)", 1.0, 20.0, 10.0)

# 3. Core Analytical Functions
def safe_parse(text):
    """Handles '<' symbols and non-numeric artifacts."""
    if pd.isna(text) or text == "": return 0.0
    s = str(text).split('<')[-1].split('!')[0].strip()
    clean = re.sub(r'[^0-9.eE-]', '', s)
    try: return float(clean) if clean else 0.0
    except: return 0.0

def parse_metadata(label):
    """Extracts Target concentration and Dilution Factor."""
    lb = str(label)
    t = re.search(r'[_.]?(\d+\.?\d*)$', lb)
    d = re.search(r'_dil(\d+\.?\d*)', lb)
    return float(t.group(1)) if t else None, float(d.group(1)) if d else 1.0

def format_output(v, is_lq=False):
    prefix = "<" if is_lq else ""
    val = max(abs(v), 1e-12) if is_lq else v
    return f"{prefix}{val:.4e}" if 0 < abs(val) < 1e-6 else f"{prefix}{val:.9f}"

# 4. Persistence Management
if 'st1_data' not in st.session_state: st.session_state.st1_data = None
if 'final_tables' not in st.session_state: st.session_state.final_tables = None

# 5. Main Application UI
st.title("🧪 ElementaQ: Analytical Audit Suite (v2.6)")
st.caption("PhD-Level Metrology Engine | Fixed Sidebar & Hybrid Drift Logic")

uploaded_file = st.file_uploader("Upload Qtegra CSV File", type="csv")

if uploaded_file:
    raw = pd.read_csv(uploaded_file)
    raw.columns = raw.columns.str.strip()
    elements = [c for c in raw.columns if c not in ['Category', 'Label', 'Type']]

    # STEP 1: INDEXING & RSD CHECK
    if st.button("📊 Step 1: Process Structure & RSD"):
        processed = []
        # Group Qtegra 4-row blocks into a single indexed record
        for i in range(0, len(raw) - (len(raw)%4), 4):
            block = raw.iloc[i:i+4]
            lbl = str(block['Label'].iloc[0]).strip()
            tp = str(block['Type'].iloc[0]).strip().upper()
            row = {'Index': len(processed) + 1, 'Label': lbl, 'Type': tp}
            for el in elements:
                avg_raw = str(block[block['Category'].str.contains('average', case=False)][el].values[0])
                rsd_v = safe_parse(block[block['Category'].str.contains('RSD', case=False)][el].values[0])
                val = safe_parse(avg_raw)
                txt = format_output(val, '<' in avg_raw)
                if '<' not in avg_raw and rsd_v > rsd_limit: txt += "!!"
                row[el] = txt
            processed.append(row)
        st.session_state.st1_data = pd.DataFrame(processed)

    if st.session_state.st1_data is not None:
        st.write("### Table 1: Indexed Input Data")
        st.dataframe(st.session_state.st1_data)

        # STEP 2: HYBRID DRIFT & MASTER EQUATION
        if st.button("🚀 Step 2: Run Metrological Correction"):
            p1 = st.session_state.st1_data.copy()
            p1['Target'], p1['Dilution'] = zip(*p1['Label'].map(parse_metadata))
            
            res_rows, audit_rows = [], []

            for _, row in p1.iterrows():
                idx = row['Index']
                res_entry = {'Index': idx, 'Label': row['Label'], 'Type': row['Type']}
                aud_entry = {'Index': idx, 'Label': row['Label'], 'Type': row['Type']}

                for el in elements:
                    # Filter valid CCVs within 10% limit
                    ccv_pool = []
                    all_ccvs = p1[(p1['Type'] == 'CCV') & (p1['Target'].notnull())]
                    for _, c in all_ccvs.iterrows():
                        m_val = safe_parse(c[el])
                        if m_val != 0:
                            recovery = (m_val / c['Target']) * 100
                            if (100 - drift_threshold) <= recovery <= (100 + drift_threshold):
                                ccv_pool.append({'idx': c['Index'], 'meas': m_val, 'target': c['Target']})
                    
                    if not ccv_pool:
                        res_entry[el], aud_entry[el] = "NO VALID CCV", "N/A"
                        continue

                    # Hybrid Drift Model Selection
                    if len(ccv_pool) == 1:
                        # Point-Correction Logic
                        f_i = ccv_pool[0]['target'] / ccv_pool[0]['meas']
                    else:
                        # Linear Interpolation Logic
                        c_idxs = [c['idx'] for c in ccv_pool]
                        if idx <= c_idxs[0]: 
                            f_i = ccv_pool[0]['target'] / ccv_pool[0]['meas']
                        elif idx >= c_idxs[-1]: 
                            f_i = ccv_pool[-1]['target'] / ccv_pool[-1]['meas']
                        else:
                            for n in range(len(c_idxs)-1):
                                if c_idxs[n] <= idx <= c_idxs[n+1]:
                                    f_start = ccv_pool[n]['target'] / ccv_pool[n]['meas']
                                    f_end = ccv_pool[n+1]['target'] / ccv_pool[n+1]['meas']
                                    f_i = f_start + (f_end - f_start) * (idx - c_idxs[n]) / (c_idxs[n+1] - c_idxs[n])
                                    break
                    
                    # Calculate Drift-Corrected Blank
                    blanks = p1[p1['Type'] == 'BLK']
                    # Blanks are drift-corrected to the start standard for baseline stability
                    drift_blanks = [safe_parse(b[el]) * (ccv_pool[0]['target'] / ccv_pool[0]['meas']) for _, b in blanks.iterrows()]
                    avg_blank = np.mean(drift_blanks) if drift_blanks else 0.0

                    # Master Equation Sequence
                    # 1. Drift Correction -> 2. Blank Subtraction
