import streamlit as st
import pandas as pd
import re
import io
import numpy as np

# --- 1. CONFIGURATION & UI ---
st.set_page_config(page_title="ElementaQ Pro", layout="wide")
st.title("🧪 ElementaQ: Analytical Engine v10.0")

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

# --- 3. PROCESSING ---
file = st.file_uploader("Upload CSV", type="csv")

if file:
    df_raw = pd.read_csv(file)
    cols = [c for c in df_raw.columns if c not in ['Category', 'Label', 'Type']]
    
    if st.button("🚀 Run Analysis"):
        # STEP 1: Parse Blocks using Category
        blocks = []
        for i in range(0, len(df_raw), 4):
            if i + 3 >= len(df_raw): break
            chunk = df_raw.iloc[i:i+4]
            
            # Find the row where Category is "Concentration average"
            # We use .str.contains to be safe against leading/trailing spaces
            avg_row = chunk[chunk['Category'].str.contains("Concentration average", na=False, case=False)]
            
            if not avg_row.empty:
                label = str(avg_row['Label'].iloc[0])
                rtype = str(avg_row['Type'].iloc[0])
                data_row = {'Label': label, 'Type': rtype}
                for c in cols:
                    data_row[c] = to_float(avg_row[c].values[0])
                blocks.append(data_row)
        
        df_clean = pd.DataFrame(blocks)

        # STEP 2: Calculate Session-Wide Drift (f)
        global_f = {c: [] for c in cols}
        for _, row in df_clean.iterrows():
            nom = get_target(row['Type'])
            if "CCV" in str(row['Type']) and nom:
                for c in cols:
                    meas = row[c]
                    if meas > 0:
                        err = abs((meas - nom) / nom) * 100
                        if db_val < err <= max_c:
                            global_f[c].append(nom / meas)
        
        final_f = {c: (np.mean(global_f[c]) if global_f[c] else 1.0) for c in cols}

        # STEP 3: Final Tables (Results & Audit)
        avg_blk = {c: df_clean[df_clean['Type'] == 'BLK'][c].mean() if not df_clean[df_clean['Type'] == 'BLK'].empty else 0.0 for c in cols}
        
        t2_res, t3_res = [], []
        for _, row in df_clean.iterrows():
            rt, lb = str(row['Type']), str(row['Label'])
            t2_r, t3_r = {'Label': lb, 'Type': rt}, {'Label': lb}
            
            dil = 1
            if '_dil' in lb:
                m = re.search(r'_dil(\d+)', lb)
                if m: dil = int(m.group(1))

            for c in cols:
                raw, f = row[c], final_f[c]
                b = avg_blk.get(c, 0) if rt == 'S' else 0.0
                
                # Math: (Raw * f - Blank) * Dilution
                val = round(max(0, (raw * f) - b) * dil, 4)
                t2_r[c] = val
                
                if rt in ['S', 'MBB', 'BLK']:
                    f_txt = f"{f:.3f}" if f != 1.0 else "1"
                    t3_r[c] = f"({raw}*{f_txt}-{b:.3f})*{dil}"
            
            t2_res.append(t2_r)
            if rt in ['S', 'MBB', 'BLK']: t3_res.append(t3_r)

        st.session_state['data'] = (df_clean, pd.DataFrame(t2_res), pd.DataFrame(t3_res), final_f)

    # --- 4. OUTPUT ---
    if 'data' in st.session_state:
        s1, s2, s3, f_applied = st.session_state['data']
        
        # Excel
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as wr:
            s1.to_excel(wr, sheet_name='1_Raw', index=False)
            s2.to_excel(wr, sheet_name='2_Final', index=False)
            s3.to_excel(wr, sheet_name='3_Audit', index=False)
        st.download_button("📥 Download Excel Report", output.getvalue(), "ICP_Report.xlsx")

        tab_raw, tab_fin, tab_aud = st.tabs(["📊 Raw Data", "✅ Final Results", "🔍 Audit Trail"])
        with tab_raw: st.dataframe(s1, use_container_width=True)
        with tab_fin: st.dataframe(s2, use_container_width=True)
        with tab_aud:
            st.info(f"Drift Correction (f): { {k: round(v,4) for k,v in f_applied.items() if v != 1.0} }")
            st.dataframe(s3, use_container_width=True)
