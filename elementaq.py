import pandas as pd
import numpy as np
import streamlit as st
import re
from io import BytesIO

# --- 1. INTERFACE ---
st.set_page_config(layout="wide", title="Rosen ICP Processor")
st.title("🔬 ICP-OES Data Processor")

if 't1' not in st.session_state: st.session_state.t1 = None
if 't2' not in st.session_state: st.session_state.t2 = None
if 't3' not in st.session_state: st.session_state.t3 = None

c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    uploaded_file = st.file_uploader("Upload ICP CSV", type="csv")
with c2:
    process_btn = st.button("🚀 Start Processing", use_container_width=True)
with c3:
    st.info("Ready" if uploaded_file else "Waiting...")

# --- 2. CORE LOGIC (GUIDE COMPLIANT) ---

def get_suffix(type_val):
    if pd.isna(type_val): return None
    match = re.search(r'_([\d.]+)$', str(type_val).strip())
    return float(match.group(1)) if match else None

def process_data(df):
    df.columns = [c.strip() for c in df.columns]
    elements = [c for c in df.columns if c not in ['Category', 'Label', 'Type']]
    
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

    all_ccvs = [b for b in blocks if 'CCV' in str(b['Type'])]
    t1_list, t2_list, t3_list = [], [], []
    
    # Pre-calculate Drift for ALL blocks (including BLK and S)
    for b in blocks:
        b['drift_factors'] = {}
        b['ccv_names'] = {}
        
        for el in elements:
            raw_val = pd.to_numeric(b['avg'][el], errors='coerce')
            f_drift, ccv_ref = 1.0, "None"
            
            # Find closest CCV within 20% window of raw value
            matches = []
            for ccv in all_ccvs:
                target = get_suffix(ccv['Type'])
                measured = pd.to_numeric(ccv['avg'][el], errors='coerce')
                if target and measured and measured > 0:
                    if (0.8 * raw_val) <= target <= (1.2 * raw_val):
                        matches.append({'f': target/measured, 'dist': abs(ccv['idx']-b['idx']), 'name': f"CCV_{target}"})
            
            if matches:
                best = min(matches, key=lambda x: x['dist'])
                f_drift, ccv_ref = best['f'], best['name']
            
            b['drift_factors'][el] = f_drift
            b['ccv_names'][el] = ccv_ref

    # Calculate Average Drift-Corrected Blank
    corrected_blanks = {el: [] for el in elements}
    for b in blocks:
        if str(b['Type']).upper() == 'BLK':
            for el in elements:
                val = pd.to_numeric(b['avg'][el], errors='coerce')
                if not pd.isna(val):
                    corrected_blanks[el].append(val * b['drift_factors'][el])
    
    avg_blank = {el: (np.mean(corrected_blanks[el]) if corrected_blanks[el] else 0.0) for el in elements}

    # Final Loop for Tables
    for b in blocks:
        # T1: Thresholds (remains raw as per usual)
        t1_row = {'Label': b['Label'], 'Type': b['Type']}
        for el in elements:
            val = pd.to_numeric(b['avg'][el], errors='coerce')
            mql = pd.to_numeric(b['sd'][el], errors='coerce') * 10
            rsd = pd.to_numeric(b['rsd'][el], errors='coerce')
            if pd.isna(val): t1_row[el] = "N/A"
            elif val < mql: t1_row[el] = f"<{mql:.3f}"
            else: t1_row[el] = f"{val:.4f}{'!!' if rsd>10 else ('!' if rsd>6 else '')}"
        t1_list.append(t1_row)

        # T2 & T3: Math (S only)
        if str(b['Type']).startswith('S'):
            t2_row, t3_row = {'Label': b['Label']}, {'Label': b['Label']}
            dil = get_suffix(b['Type']) or 1.0
            
            for el in elements:
                raw_val = pd.to_numeric(b['avg'][el], errors='coerce')
                if pd.isna(raw_val): continue
                
                f = b['drift_factors'][el]
                ccv = b['ccv_names'][el]
                b_val = avg_blank[el]
                
                # Formula: (Raw * Drift - Blank) * Dilution
                res = (raw_val * f - b_val) * dil
                
                t2_row[el] = f"{res:.4f}"
                t3_row[el] = f"({raw_val:.3f} * {f:.3f}[{ccv}] - {b_val:.3f}[BLK]) * {dil}"
            
            t2_list.append(t2_row)
            t3_list.append(t3_row)

    return pd.DataFrame(t1_list), pd.DataFrame(t2_list), pd.DataFrame(t3_list)

# --- 3. RENDERING ---
if uploaded_file and process_btn:
    st.session_state.t1, st.session_state.t2, st.session_state.t3 = process_data(pd.read_csv(uploaded_file))

if st.session_state.t1 is not None:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        st.session_state.t1.to_excel(writer, sheet_name='Thresholds', index=False)
        st.session_state.t2.to_excel(writer, sheet_name='Final_Results', index=False)
        st.session_state.t3.to_excel(writer, sheet_name='Math_Log', index=False)
    
    st.download_button("📥 Download Report", buf.getvalue(), "ICP_Report.xlsx")
    tabs = st.tabs(["📊 Thresholds", "✅ Final Results", "📝 Math Log"])
    tabs[0].dataframe(st.session_state.t1)
    tabs[1].dataframe(st.session_state.t2)
    tabs[2].dataframe(st.session_state.t3)
