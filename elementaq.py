import pandas as pd
import numpy as np
import streamlit as st
import re
from io import BytesIO

# --- 1. INTERFACE (TRIPLE COLUMN TOP BAR) ---
st.set_page_config(layout="wide", page_title="Rosen ICP Processor")
st.title("🔬 ICP-OES Data Processor")

if 't1' not in st.session_state:
    st.session_state.t1 = None
    st.session_state.t2 = None
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

# --- 2. ANALYTICAL CORE (AGREEMENT COMPLIANT) ---

def extract_numeric_suffix(type_str):
    """
    Agreement Rule: Look for the last underscore and extract the number.
    Works for both CCV concentrations (CCV_0.1) and Dilutions (Sample_dil10).
    """
    if pd.isna(type_str): return None
    type_str = str(type_str).strip()
    
    # Using regex to find the last number after underscore
    match = re.search(r'_(\d+\.?\d*)$', type_str)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None

def run_processor(df):
    # Sanitize column names
    df.columns = [c.strip() for c in df.columns]
    # Identify element columns (exclude metadata)
    metadata_cols = ['Category', 'Label', 'Type']
    elements = [c for c in df.columns if c not in metadata_cols]
    
    blocks = []
    # Data is grouped in 4-line blocks (Avg, SD, RSD, MQL)
    for i in range(0, len(df), 4):
        sub = df.iloc[i:i+4]
        if len(sub) < 4: continue
        
        try:
            avg = sub[sub['Category'].str.contains('average', case=False)].iloc[0]
            sd = sub[sub['Category'].str.contains('SD', case=False)].iloc[0]
            rsd = sub[sub['Category'].str.contains('RSD', case=False)].iloc[0]
            blocks.append({
                'idx': i, 
                'Label': avg['Label'], 
                'Type': avg['Type'], 
                'avg': avg, 
                'sd': sd, 
                'rsd': rsd
            })
        except (IndexError, KeyError):
            continue

    t1_list, t2_list, t3_list = [], [], []
    all_ccvs = [b for b in blocks if 'CCV' in str(b['Type'])]

    for b in blocks:
        # TABLE 1: Thresholds & RSD Flags
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
        t1_list.append(t1_r)

        # TABLE 2 & 3: Final Calculations (Only for Samples 'S')
        # Logic: S can also be 'S_dil10' according to Agreement
        if str(b['Type']).startswith('S'):
            t2_r, t3_r = {'Label': b['Label']}, {'Label': b['Label']}
            
            # Extract dilution from Type (e.g., S_dil50 -> 50)
            dil_factor = extract_numeric_suffix(b['Type'])
            if dil_factor is None: dil_factor = 1.0
            
            for el in elements:
                c_raw = pd.to_numeric(b['avg'][el], errors='coerce')
                f_drift = 1.0
                used_ccv = "None"
                
                # Match CCV within +/- 20% window (Guide Logic)
                matches = []
                for ccv_b in all_ccvs:
                    target = extract_numeric_suffix(ccv_b['Type'])
                    measured = pd.to_numeric(ccv_b['avg'][el], errors='coerce')
                    
                    if target and measured and measured > 0:
                        # Check if c_raw is within 20% of the standard target
                        if (0.8 * c_raw) <= target <= (1.2 * c_raw):
                            matches.append({
                                'f': target / measured, 
                                'dist': abs(ccv_b['idx'] - b['idx']),
                                'name': f"CCV_{target}"
                            })
                
                if matches:
                    # Pick the closest CCV in the sequence
                    best = min(matches, key=lambda x: x['dist'])
                    f_drift = best['f']
                    used_ccv = best['name']

                c_final = c_raw * f_drift * dil_factor
                
                t2_r[el] = f"{c_final:.4f}"
                t3_r[el] = f"{c_raw:.4f} * {f_drift:.3f} ({used_ccv}) * {dil_factor}"
            
            t2_list.append(t2_r)
            t3_list.append(t3_r)

    return pd.DataFrame(t1_list), pd.DataFrame(t2_list), pd.DataFrame(t3_list)

# --- 3. OUTPUT & RENDERING ---

if uploaded_file and process_btn:
    raw_data = pd.read_csv(uploaded_file)
    t1, t2, t3 = run_processor(raw_data)
    st.session_state.t1, st.session_state.t2, st.session_state.t3 = t1, t2, t3

if st.session_state.t1 is not None:
    # Excel Export
    output_buffer = BytesIO()
    with pd.ExcelWriter(output_buffer, engine='xlsxwriter') as writer:
        st.session_state.t1.to_excel(writer, sheet_name='1_Thresholds', index=False)
        st.session_state.t2.to_excel(writer, sheet_name='2_Final_Results', index=False)
        st.session_state.t3.to_excel(writer, sheet_name='3_Math_Log', index=False)
    
    st.download_button(
        label="📥 Download Excel Report", 
        data=output_buffer.getvalue(), 
        file_name="ICP_Final_Report.xlsx", 
        mime="application/vnd.ms-excel"
    )

    # Tabs display
    tabs = st.tabs(["📊 1. Thresholds", "✅ 2. Final Results", "📝 3. Math Log"])
    tabs[0].dataframe(st.session_state.t1, use_container_width=True)
    tabs[1].dataframe(st.session_state.t2, use_container_width=True)
    tabs[2].dataframe(st.session_state.t3, use_container_width=True)
