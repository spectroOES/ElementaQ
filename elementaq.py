import streamlit as st
import pandas as pd
import numpy as np
import re
import io
from io import BytesIO

# --- 1. SETTINGS & INTERFACE ---
st.set_page_config(layout="wide", page_title="Rosen ICP Processor", page_icon="🧪")

if 't1' not in st.session_state: st.session_state.t1 = None
if 't2' not in st.session_state: st.session_state.t2 = None
if 't3' not in st.session_state: st.session_state.t3 = None

st.title("🧪 ICP-OES Analytical Processor")

# Sidebar for RSD limits from your reference code
st.sidebar.header("RSD Control Limits")
rsd_low = st.sidebar.slider("Yellow Flag (!)", 1.0, 15.0, 6.0, 0.5)
rsd_high = st.sidebar.slider("Red Flag (!!)", 1.0, 25.0, 10.0, 0.5)

c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    uploaded_file = st.file_uploader("Upload Source CSV", type="csv")
with c2:
    process_btn = st.button("🚀 Start Full Processing", use_container_width=True)
with c3:
    st.write("**Status:**")
    st.info("Ready" if uploaded_file else "Waiting...")

# --- 2. ANALYTICAL UTILITIES ---

def get_type_suffix(type_val):
    """Agreement: Extract numeric value after last underscore in Type column."""
    if pd.isna(type_val): return None
    s = str(type_val).strip()
    match = re.search(r'_([\d.]+)$', s)
    return float(match.group(1)) if match else None

def is_below_loq(avg_val, mql_val):
    """Strict logic from your code: check for <LQ text or value below MQL."""
    if pd.isna(avg_val): return True
    s_val = str(avg_val).strip()
    if "<LQ" in s_val: return True
    try:
        num_val = float(s_val)
        return num_val < mql_val
    except ValueError:
        return True

def to_num(val):
    """Safe conversion to float for calculation only if it's a pure number."""
    try: return float(str(val).strip())
    except: return None

# --- 3. CORE ANALYTICAL PROCESS ---

def process_full_logic(df, r_low, r_high):
    df.columns = df.columns.str.strip()
    elements = [c for c in df.columns if c not in ['Category', 'Label', 'Type']]
    
    blocks = []
    # Block of 4 rows logic
    for i in range(0, len(df) - (len(df) % 4), 4):
        sub = df.iloc[i : i+4]
        try:
            avg = sub[sub['Category'].str.contains('average', case=False, na=False)].iloc[0]
            sd  = sub[sub['Category'].str.contains('SD', case=False, na=False)].iloc[0]
            rsd = sub[sub['Category'].str.contains('RSD', case=False, na=False)].iloc[0]
            mql = sub[sub['Category'].str.contains('MQL', case=False, na=False)].iloc[0]
            
            blocks.append({
                'idx': i, 'Label': avg['Label'], 'Type': avg['Type'],
                'avg_row': avg, 'sd_row': sd, 'rsd_row': rsd, 'mql_row': mql,
                'drift_f': {}, 'ccv_name': {}
            })
        except: continue

    all_ccvs = [b for b in blocks if 'CCV' in str(b['Type'])]

    # PHASE 1: DRIFT CORRECTION (ONLY FOR VALUES ABOVE LOQ)
    for b in blocks:
        for el in elements:
            f_drift, ccv_ref = 1.0, "None"
            mql_val = to_num(b['mql_row'][el]) or 0.0
            
            # If value is below LOQ, drift is not calculated (stays 1.0)
            if not is_below_loq(b['avg_row'][el], mql_val):
                raw_num = to_num(b['avg_row'][el])
                matches = []
                for ccv in all_ccvs:
                    target = get_type_suffix(ccv['Type'])
                    measured = to_num(ccv['avg_row'][el])
                    if target and measured and measured > 0:
                        if (0.8 * raw_num) <= target <= (1.2 * raw_num):
                            matches.append({
                                'f': target / measured, 
                                'dist': abs(ccv['idx'] - b['idx']),
                                'name': f"CCV_{target}"
                            })
                if matches:
                    best = min(matches, key=lambda x: x['dist'])
                    f_drift, ccv_ref = best['f'], best['name']
            
            b['drift_f'][el] = f_drift
            b['ccv_name'][el] = ccv_ref

    # PHASE 2: MEAN CORRECTED BLANK
    avg_corrected_blank = {}
    for el in elements:
        blank_vals = []
        for b in blocks:
            if any(x in str(b['Type']).upper() for x in ['BLK', 'MBB']):
                mql_v = to_num(b['mql_row'][el]) or 0.0
                # Blank is only used if it's above its own MQL (rare but following logic)
                # or we take it as raw if your guide allows. Usually BLKs are used as is:
                val = to_num(b['avg_row'][el])
                if val is not None:
                    blank_vals.append(val * b['drift_f'][el])
        avg_corrected_blank[el] = np.mean(blank_vals) if blank_vals else 0.0

    # PHASE 3: FINAL TABLES
    t1_list, t2_list, t3_list = [], [], []

    for b in blocks:
        t1_r = {'Label': b['Label'], 'Type': b['Type']}
        for el in elements:
            mql_v = to_num(b['mql_row'][el]) or 0.0
            avg_raw = b['avg_row'][el]
            
            if is_below_loq(avg_raw, mql_v):
                sd_v = to_num(b['sd_row'][el]) or 0.0
                t1_r[el] = f"<{round(sd_v * 10, 3)}"
            else:
                num = to_num(avg_raw)
                rsd_v = to_num(b['rsd_row'][el]) or 0.0
                flag = "!!" if rsd_v > r_high else ("!" if rsd_v > r_low else "")
                t1_r[el] = f"{num}{flag}"
        t1_list.append(t1_r)

        if str(b['Type']).startswith('S'):
            t2_r, t3_r = {'Label': b['Label']}, {'Label': b['Label']}
            dil = get_type_suffix(b['Type']) or 1.0
            for el in elements:
                mql_v = to_num(b['mql_row'][el]) or 0.0
                if is_below_loq(b['avg_row'][el], mql_v):
                    t2_r[el] = "N.D."
                    t3_r[el] = "Below LOQ"
                else:
                    val = to_num(b['avg_row'][el])
                    f = b['drift_f'][el]
                    ccv = b['ccv_name'][el]
                    b_c = avg_corrected_blank[el]
                    res = (val * f - b_c) * dil
                    t2_r[el] = f"{res:.4f}"
                    t3_r[el] = f"({val:.3f} * {f:.3f}[{ccv}] - {b_c:.3f}[BLK]) * {dil}"
            t2_list.append(t2_r)
            t3_list.append(t3_r)

    return pd.DataFrame(t1_list), pd.DataFrame(t2_list), pd.DataFrame(t3_list)

# --- 4. OUTPUT ---
if uploaded_file and process_btn:
    raw_df = pd.read_csv(uploaded_file)
    st.session_state.t1, st.session_state.t2, st.session_state.t3 = process_full_logic(raw_df, rsd_low, rsd_high)

if st.session_state.t1 is not None:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        st.session_state.t1.to_excel(writer, sheet_name='Thresholds', index=False)
        st.session_state.t2.to_excel(writer, sheet_name='Final_Results', index=False)
        st.session_state.t3.to_excel(writer, sheet_name='Math_Log', index=False)
    
    st.download_button("📥 DOWNLOAD EXCEL REPORT", buf.getvalue(), "ICP_Report.xlsx")
    tabs = st.tabs(["📊 1. Thresholds", "✅ 2. Final Results", "📝 3. Math Log"])
    tabs[0].dataframe(st.session_state.t1, use_container_width=True)
    tabs[1].dataframe(st.session_state.t2, use_container_width=True)
    tabs[2].dataframe(st.session_state.t3, use_container_width=True)
