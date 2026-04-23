import pandas as pd
import numpy as np
import streamlit as st
import re
from io import BytesIO

# --- 1. INTERFACE CONFIGURATION ---
st.set_page_config(layout="wide", page_title="Rosen ICP Processor")
st.title("🔬 ICP-OES Data Processor")

# Initialize session state keys to prevent NameError before processing
if 't1' not in st.session_state:
    st.session_state.t1 = None
if 't2' not in st.session_state:
    st.session_state.t2 = None
if 't3' not in st.session_state:
    st.session_state.t3 = None

# Top Bar Interface
c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    uploaded_file = st.file_uploader("Upload ICP CSV", type="csv")
with c2:
    # Trigger processing only on click
    process_btn = st.button("🚀 Start Processing", use_container_width=True)
with c3:
    st.write("**System Status:**")
    if uploaded_file:
        st.success("File Ready")
    else:
        st.info("Waiting for file...")

st.markdown("---")

# --- 2. ANALYTICAL ENGINE ---

def get_numeric_suffix(type_val):
    """Agreement: Extract numeric value after last underscore in Type column."""
    if pd.isna(type_val): return None
    s = str(type_val).strip()
    match = re.search(r'_([\d.]+)$', s)
    if match:
        try: return float(match.group(1))
        except: return None
    return None

def run_analytical_process(df):
    # Sanitize and identify elements
    df.columns = [c.strip() for c in df.columns]
    elements = [c for c in df.columns if c not in ['Category', 'Label', 'Type']]
    
    # Parse data blocks (4 lines each)
    blocks = []
    for i in range(0, len(df), 4):
        sub = df.iloc[i:i+4]
        if len(sub) < 4: continue
        try:
            avg = sub[sub['Category'].str.contains('average', case=False)].iloc[0]
            sd = sub[sub['Category'].str.contains('SD', case=False)].iloc[0]
            rsd = sub[sub['Category'].str.contains('RSD', case=False)].iloc[0]
            blocks.append({
                'idx': i, 'Label': avg['Label'], 'Type': avg['Type'],
                'avg': avg, 'sd': sd, 'rsd': rsd,
                'drift_f': {}, 'ccv_name': {}
            })
        except: continue

    all_ccvs = [b for b in blocks if 'CCV' in str(b['Type'])]

    # PHASE 1: INDIVIDUAL DRIFT CALCULATION (FOR ALL: S AND BLK)
    # Applied to every block before blank subtraction as per Workflow Guide
    for b in blocks:
        for el in elements:
            raw_val = pd.to_numeric(b['avg'][el], errors='coerce')
            f_drift, ccv_ref = 1.0, "None"
            
            if not pd.isna(raw_val):
                matches = []
                for ccv in all_ccvs:
                    target = get_numeric_suffix(ccv['Type'])
                    measured = pd.to_numeric(ccv['avg'][el], errors='coerce')
                    if target and measured and measured > 0:
                        # Drift window match check (+/- 20%)
                        if (0.8 * raw_val) <= target <= (1.2 * raw_val):
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

    # PHASE 2: CALCULATE MEAN CORRECTED BLANK
    # First correct each blank's drift, then calculate the average
    avg_corrected_blank = {}
    for el in elements:
        blank_vals = []
        for b in blocks:
            if any(x in str(b['Type']).upper() for x in ['BLK', 'MBB']):
                val = pd.to_numeric(b['avg'][el], errors='coerce')
                if not pd.isna(val):
                    blank_vals.append(val * b['drift_f'][el])
        avg_corrected_blank[el] = np.mean(blank_vals) if blank_vals else 0.0

    # PHASE 3: GENERATE RESULT TABLES
    t1_rows, t2_rows, t3_rows = [], [], []

    for b in blocks:
        # Table 1: Raw Thresholds & Flags
        t1_r = {'Label': b['Label'], 'Type': b['Type']}
        for el in elements:
            val = pd.to_numeric(b['avg'][el], errors='coerce')
            mql = pd.to_numeric(b['sd'][el], errors='coerce') * 10
            rsd_val = pd.to_numeric(b['rsd'][el], errors='coerce')
            if pd.isna(val): t1_r[el] = "N/A"
            elif val < mql: t1_r[el] = f"<{mql:.3f}"
            else:
                flag = "!!" if rsd_val > 10 else ("!" if rsd_val > 6 else "")
                t1_r[el] = f"{val:.4f}{flag}"
        t1_rows.append(t1_r)

        # Table 2 & 3: Final Math (Only for Samples)
        if str(b['Type']).startswith('S'):
            t2_r, t3_r = {'Label': b['Label']}, {'Label': b['Label']}
            dil = get_numeric_suffix(b['Type']) or 1.0
            
            for el in elements:
                raw_c = pd.to_numeric(b['avg'][el], errors='coerce')
                if pd.isna(raw_c): continue
                
                f = b['drift_f'][el]
                ccv = b['ccv_name'][el]
                b_corr = avg_corrected_blank[el]
                
                # Formula: (Raw * Drift - AvgCorrectedBlank) * Dilution
                final_val = (raw_c * f - b_corr) * dil
                
                t2_r[el] = f"{final_val:.4f}"
                t3_r[el] = f"({raw_c:.3f} * {f:.3f}[{ccv}] - {b_corr:.3f}[BLK]) * {dil}"
            
            t2_rows.append(t2_r)
            t3_rows.append(t3_r)

    return pd.DataFrame(t1_rows), pd.DataFrame(t2_rows), pd.DataFrame(t3_rows)

# --- 3. EXECUTION AND OUTPUT ---

if uploaded_file and process_btn:
    # Run engine and save to session state
    df_raw = pd.read_csv(uploaded_file)
    st.session_state.t1, st.session_state.t2, st.session_state.t3 = run_analytical_process(df_raw)

# Render only if calculations were performed
if st.session_state.t1 is not None:
    # Export to Excel
    export_buf = BytesIO()
    with pd.ExcelWriter(export_buf, engine='xlsxwriter') as writer:
        st.session_state.t1.to_excel(writer, sheet_name='1_Thresholds', index=False)
        st.session_state.t2.to_excel(writer, sheet_name='2_Final_Results', index=False)
        st.session_state.t3.to_excel(writer, sheet_name='3_Math_Log', index=False)
    
    st.download_button("📥 Download Excel Report", export_buf.getvalue(), "ICP_Report.xlsx")
    
    # Display Result Tabs
    tabs = st.tabs(["📊 1. Thresholds", "✅ 2. Final Results", "📝 3. Math Log"])
    tabs[0].dataframe(st.session_state.t1, use_container_width=True)
    tabs[1].dataframe(st.session_state.t2, use_container_width=True)
    tabs[2].dataframe(st.session_state.t3, use_container_width=True)
