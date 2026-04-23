import streamlit as st
import pandas as pd
import re
import io
import numpy as np

# --- 1. SETTINGS & UI ---
st.set_page_config(page_title="ElementaQ Pro", layout="wide")
st.title("🧪 ElementaQ: Analytical Engine v11.0")

# Permanent Sidebar
with st.sidebar:
    st.header("⚙️ QC Settings")
    rsd_l = st.slider("Yellow Flag RSD %", 1.0, 15.0, 6.0, key='rsd_l')
    rsd_h = st.slider("Red Flag RSD %", 5.0, 30.0, 10.0, key='rsd_h')
    st.markdown("---")
    st.header("📈 Drift Settings")
    db_val = st.number_input("Deadband (No correction < %)", 0.0, 10.0, 5.0, key='db')
    max_c = st.number_input("Max Correction (%)", 5.0, 50.0, 20.0, key='mc')

# --- 2. HELPER FUNCTIONS ---
def to_float(val):
    if pd.isna(val): return 0.0
    if isinstance(val, str):
        val = re.sub(r'[^\d\.]', '', val.split('<')[0])
    try: return float(val)
    except: return 0.0

def get_target(t_str):
    res = re.search(r'_([\d\.]+)$', str(t_str))
    return float(res.group(1)) if res else None

# --- 3. CORE PROCESSING ---
file = st.file_uploader("Upload CSV Data", type="csv")

if file:
    try:
        df_raw = pd.read_csv(file)
        # Identify element columns
        cols = [c for c in df_raw.columns if c not in ['Category', 'Label', 'Type']]
        
        if st.button("🚀 Execute Analysis"):
            # STEP 1: Aggregation by "Concentration average"
            blocks = []
            # We iterate through the dataframe and pick rows marked as "Concentration average"
            avg_rows = df_raw[df_raw['Category'].str.contains("Concentration average", na=False, case=False)]
            
            for _, row in avg_rows.iterrows():
                data_row = {'Label': str(row['Label']), 'Type': str(row['Type'])}
                for c in cols:
                    data_row[c] = to_float(row[c])
                blocks.append(data_row)
            
            if not blocks:
                st.error("No 'Concentration average' rows found in the Category column!")
            else:
                df_clean = pd.DataFrame(blocks)

                # STEP 2: Global Drift Factor Calculation (f)
                global_f = {c: [] for c in cols}
                for _, row in df_clean.iterrows():
                    nom = get_target(row['Type'])
                    # Filter for CCV types with a numeric target
                    if "CCV" in str(row['Type']) and nom:
                        for c in cols:
                            meas = row[c]
                            if meas > 0:
                                err = abs((meas - nom) / nom) * 100
                                if db_val < err <= max_c:
                                    global_f[c].append(nom / meas)
                
                final_f = {c: (np.mean(global_f[c]) if global_f[c] else 1.0) for c in cols}

                # STEP 3: Final Results & Audit Table
                # Calculate mean BLK per element
                avg_blk = {c: df_clean[df_clean['Type'] == 'BLK'][c].mean() if not df_clean[df_clean['Type'] == 'BLK'].empty else 0.0 for c in cols}
                
                t2_res, t3_res = [], []
                for _, row in df_clean.iterrows():
                    rt, lb = str(row['Type']), str(row['Label'])
                    t2_r, t3_r = {'Label': lb, 'Type': rt}, {'Label': lb}
                    
                    # Dilution Check
                    dil = 1
                    if '_dil' in lb:
                        m = re.search(r'_dil(\d+)', lb)
                        if m: dil = int(m.group(1))

                    for c in cols:
                        raw, f = row[c], final_f[c]
                        # Subtract BLK only for Samples (S)
                        b = avg_blk.get(c, 0) if rt == 'S' else 0.0
                        
                        val = round(max(0, (raw * f) - b) * dil, 4)
                        t2_r[c] = val
                        
                        if rt in ['S', 'MBB', 'BLK']:
                            f_txt = f"{f:.3f}" if f != 1.0 else "1"
                            t3_r[c] = f"({raw}*{f_txt}-{b:.3f})*{dil}"
                    
                    t2_res.append(t2_r)
                    if rt in ['S', 'MBB', 'BLK']:
                        t3_res.append(t3_r)

                st.session_state['results'] = (df_clean, pd.DataFrame(t2_res), pd.DataFrame(t3_res), final_f)

    except Exception as e:
        st.error(f"Error processing file: {e}")

# --- 4. OUTPUT ---
if 'results' in st.session_state:
    s1, s2, s3, f_map = st.session_state['results']
    
    # Excel Generation
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as wr:
        s1.to_excel(wr, sheet_name='1_RawData', index=False)
        s2.to_excel(wr, sheet_name='2_FinalResults', index=False)
        s3.to_excel(wr, sheet_name='3_AuditTrail', index=False)
    
    st.download_button("📥 Download Excel Report", output.getvalue(), "ICP_Analysis_Final.xlsx")

    tabs = st.tabs(["📊 Raw Data", "✅ Final Results", "🔍 Audit Trail"])
    with tabs[0]: st.dataframe(s1, use_container_width=True)
    with tabs[1]: st.dataframe(s2, use_container_width=True)
    with tabs[2]:
        st.info(f"Drift Correction applied (f): { {k: round(v,4) for k,v in f_map.items() if v != 1.0} }")
        st.dataframe(s3, use_container_width=True)
