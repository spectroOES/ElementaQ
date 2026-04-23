import pandas as pd
import numpy as np
import streamlit as st
import re
from io import BytesIO

# --- 1. INTERFACE CONFIGURATION ---
st.set_page_config(layout="wide", page_title="Rosen ICP Processor")
st.title("🔬 ElementaQ ")

# Global variables initialization in session state to prevent NameError
if 't1' not in st.session_state:
    st.session_state.t1 = None
if 't2' not in st.session_state:
    st.session_state.t2 = None
if 't3' not in st.session_state:
    st.session_state.t3 = None

c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    uploaded_file = st.file_uploader("Upload ICP CSV", type="csv")
with c2:
    process_btn = st.button("🚀 Start Processing", use_container_width=True)
with c3:
    st.write("**System Status:**")
    if uploaded_file:
        st.success("File Ready")
    else:
        st.info("Waiting...")

st.markdown("---")

# --- 2. ANALYTICAL LOGIC (AGREEMENT COMPLIANT) ---

def get_numeric_suffix(type_value):
    """
    Agreement Rule: Find the last underscore and extract the numeric value.
    Used for CCV targets (e.g., CCV_0.1 -> 0.1) and Dilutions (e.g., S_dil50 -> 50).
    """
    if pd.isna(type_value): return None
    s = str(type_value).strip()
    match = re.search(r'_([\d.]+)$', s)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None

def process_data(df):
    # Sanitize headers
    df.columns = [c.strip() for c in df.columns]
    elements = [c for c in df.columns if c not in ['Category', 'Label', 'Type']]
    
    # 4-line block parser
    blocks = []
    for i in range(0, len(df), 4):
        sub = df.iloc[i:i+4]
        if len(sub) < 4: continue
        try:
            avg = sub[sub['Category'].str.contains('average', case=False)].iloc[0]
            sd = sub[sub['Category'].str.contains('SD', case=False)].iloc[0]
            rsd = sub[sub['Category'].str.contains('RSD', case=False)].iloc[0]
            blocks.append({'idx': i, 'Label': avg['Label'], 'Type': avg['Type'], 'avg': avg, 'sd': sd, 'rsd': rsd})
        except: continue

    t1_data, t2_data, t3_data = [], [], []
    all_ccvs = [b for b in blocks if 'CCV' in str(b['Type'])]

    for b in blocks:
        # T1: Thresholds
        t1_row = {'Label': b['Label'], 'Type': b['Type']}
        for el in elements:
            val = pd.to_numeric(b['avg'][el], errors='coerce')
            mql = pd.to_numeric(b['sd'][el], errors='coerce') * 10
            rsd_val = pd.to_numeric(b['rsd'][el], errors='coerce')
            if pd.isna(val): t1_row[el] = "N/A"
            elif val < mql: t1_row[el] = f"<{mql:.3f}"
            else:
                flag = "!!" if rsd_val > 10 else ("!" if rsd_val > 6 else "")
                t1_row[el] = f"{val:.4f}{flag}"
        t1_data.append(t1_row)

        # T2 & T3: Math (Samples only)
        if str(b['Type']).startswith('S'):
            t2_row, t3_row = {'Label': b['Label']}, {'Label': b['Label']}
            # Dilution factor strictly from Type suffix
            dil = get_numeric_suffix(b['Type'])
            dil = dil if dil is not None else 1.0
            
            for el in elements:
                c_meas = pd.to_numeric(b['avg'][el], errors='coerce')
                f_drift = 1.0
                ccv_ref = "None"
                
                # Drift correction search (+/- 20% rule)
                matches = []
                for ccv in all_ccvs:
                    target = get_numeric_suffix(ccv['Type'])
                    measured = pd.to_numeric(ccv['avg'][el], errors='coerce')
                    if target and measured and measured > 0:
                        if (0.8 * c_meas) <= target <= (1.2 * c_meas):
                            matches.append({
                                'f': target / measured, 
                                'dist': abs(ccv['idx'] - b['idx']),
                                'name': f"CCV_{target}"
                            })
                
                if matches:
                    best = min(matches, key=lambda x: x['dist'])
                    f_drift = best['f']
                    ccv_ref = best['name']

                result = c_meas * f_drift * dil
                t2_row[el] = f"{result:.4f}"
                t3_row[el] = f"{c_meas:.4f} * {f_drift:.3f} ({ccv_ref}) * {dil}"
            
            t2_data.append(t2_row)
            t3_data.append(t3_row)

    return pd.DataFrame(t1_data), pd.DataFrame(t2_data), pd.DataFrame(t3_data)

# --- 3. EXECUTION AND RENDERING ---

if uploaded_file and process_btn:
    raw_csv = pd.read_csv(uploaded_file)
    res_t1, res_t2, res_t3 = process_data(raw_csv)
    # Store in session state
    st.session_state.t1 = res_t1
    st.session_state.t2 = res_t2
    st.session_state.t3 = res_t3

# Only render if data exists in session state
if st.session_state.t1 is not None:
    # Excel Generation
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        st.session_state.t1.to_excel(writer, sheet_name='Thresholds', index=False)
        st.session_state.t2.to_excel(writer, sheet_name='Final_Results', index=False)
        st.session_state.t3.to_excel(writer, sheet_name='Calculation_Log', index=False)
    
    st.download_button("📥 Download Excel Report", buf.getvalue(), "ICP_Results.xlsx")

    # Display Tables
    tabs = st.tabs(["📊 Thresholds", "✅ Final Results", "📝 Calculation Log"])
    tabs[0].dataframe(st.session_state.t1, use_container_width=True)
    tabs[1].dataframe(st.session_state.t2, use_container_width=True)
    tabs[2].dataframe(st.session_state.t3, use_container_width=True)
