import streamlit as st
import pandas as pd
import numpy as np
import re
import io

# 1. Page Config
st.set_page_config(page_title="ElementaQ v3.0", layout="wide", page_icon="🧪")

# 2. FIXED SIDEBAR - Initialized before any logic
st.sidebar.header("Global Control Settings")
if 'rsd_val' not in st.session_state: st.session_state.rsd_val = 10.0
if 'drift_val' not in st.session_state: st.session_state.drift_val = 10.0

rsd_limit = st.sidebar.slider("RSD Threshold (%)", 1.0, 25.0, st.session_state.rsd_val)
drift_threshold = st.sidebar.slider("CCV Drift Limit (%)", 1.0, 20.0, st.session_state.drift_val)

# 3. Enhanced Core Functions (QA Cleaned)
def safe_parse(text):
    """Deep cleaning of instrumental artifacts."""
    if pd.isna(text) or text == "": return 0.0
    s = str(text).replace('<', '').replace('!', '').replace('*', '').replace(',', '.').strip()
    clean = re.sub(r'[^0-9.eE-]', '', s)
    try:
        val = float(clean) if clean else 0.0
        return val
    except: return 0.0

def get_meta(label):
    """Reliable extraction of concentration targets and dilutions."""
    lb = str(label)
    # Search for target (e.g., Mix_5.0 -> 5.0)
    t_match = re.search(r'[_.](\d+\.?\d*)$', lb)
    d_match = re.search(r'_dil(\d+\.?\d*)', lb)
    return (float(t_match.group(1)) if t_match else None, 
            float(d_match.group(1)) if d_match else 1.0)

def format_output(v, is_lq=False):
    prefix = "<" if is_lq else ""
    return f"{prefix}{v:.4e}" if 0 < abs(v) < 1e-6 else f"{prefix}{v:.9f}"

# 4. Main Engine
st.title("🧪 ElementaQ: Analytical Audit Suite (v3.0)")
st.caption("QA-Validated Engine | Stable Metrology | Master Equation")

uploaded_file = st.file_uploader("Upload Qtegra CSV File", type="csv")

if uploaded_file:
    # Read and clean headers immediately
    raw = pd.read_csv(uploaded_file)
    raw.columns = [c.strip() for c in raw.columns]
    elements = [c for c in raw.columns if c not in ['Category', 'Label', 'Type']]

    if st.button("📊 Step 1: Process Structure & Meta-Data"):
        processed = []
        for i in range(0, len(raw) - (len(raw)%4), 4):
            block = raw.iloc[i:i+4]
            lbl = str(block['Label'].iloc[0]).strip()
            # Normalize Type for matching
            tp = str(block['Type'].iloc[0]).strip().upper()
            
            target, dilution = get_meta(lbl)
            row = {'Index': (i//4)+1, 'Label': lbl, 'Type': tp, 'Target': target, 'Dilution': dilution}
            
            for el in elements:
                # Find the 'average' and 'RSD' rows in the block
                avg_str = str(block[block['Category'].str.contains('average', case=False, na=False)][el].values[0])
                rsd_val = safe_parse(block[block['Category'].str.contains('RSD', case=False, na=False)][el].values[0])
                
                val = safe_parse(avg_str)
                txt = format_output(val, '<' in avg_str)
                if '<' not in avg_str and rsd_val > rsd_limit: txt += "!!"
                row[el] = txt
                
            processed.append(row)
        st.session_state.st1_data = pd.DataFrame(processed)

    if st.session_state.st1_data is not None:
        st.write("### Table 1: Indexed Input Data")
        st.dataframe(st.session_state.st1_data)

        if st.button("🚀 Step 2: Final Metrological Calculation"):
            df = st.session_state.st1_data.copy()
            res_rows, audit_rows = [], []

            for _, row in df.iterrows():
                curr_idx = row['Index']
                res_entry = {'Index': curr_idx, 'Label': row['Label'], 'Type': row['Type']}
                aud_entry = {'Index': curr_idx, 'Label': row['Label'], 'Type': row['Type']}

                for el in elements:
                    # 1. DRIFT CORRECTION LOGIC [cite: 30-39, 188-190]
                    # Identify valid CCVs for THIS specific element
                    ccv_pool = []
                    all_ccvs = df[(df['Type'].str.contains('CCV', na=False)) & (df['Target'].notnull())]
                    
                    for _, c in all_ccvs.iterrows():
                        meas = safe_parse(c[el])
                        if meas > 0:
                            rec = (meas / c['Target']) * 100
                            if (100 - drift_threshold) <= rec <= (100 + drift_threshold):
                                ccv_pool.append({'idx': c['Index'], 'f': c['Target']/meas})
                    
                    if not ccv_pool:
                        res_entry[el], aud_entry[el] = "NO VALID CCV", "FAIL"
                        continue

                    # Determine factor fi using Hybrid Model [cite: 61, 94]
                    if len(ccv_pool) == 1:
                        fi = ccv_pool[0]['f']
                    else:
                        idxs = [c['idx'] for c in ccv_pool]
                        if curr_idx <= idxs[0]: fi = ccv_pool[0]['f']
                        elif curr_idx >= idxs[-1]: fi = ccv_pool[-1]['f']
                        else:
                            for n in range(len(idxs)-1):
                                if idxs[n] <= curr_idx <= idxs[n+1]:
                                    f_s, f_e = ccv_pool[n]['f'], ccv_pool[n+1]['f']
                                    fi = f_s + (f_e - f_s) * (curr_idx - idxs[n]) / (idxs[n+1] - idxs[n])
                                    break
                    
                    # 2. MASTER EQUATION WORKFLOW [cite: 205-207, 219-221]
                    raw_val = safe_parse(row[el])
                    # Phase A: Drift
                    c_drift = raw_val * (fi if row['Type'] in ['S', 'BLK', 'MBB'] else 1.0)
                    
                    # Phase B: Blank Subtraction
                    blks = df[df['Type'].isin(['BLK', 'MBB'])]
                    # Blanks are normalized to initial sensitivity baseline
                    drift_blks = [safe_parse(b[el]) * ccv_pool[0]['f'] for _, b in blks.iterrows()]
                    avg_b = np.mean(drift_blks) if drift_blks else 0.0
                    
                    c_net = c_drift - (avg_b if row['Type'] == 'S' else 0.0)
                    
                    # Phase C: Dilution
                    final_conc = c_net * row['Dilution']
                    
                    res_entry[el] = format_output(final_conc, '<' in str(row[el]))
                    aud_entry[el] = f"f:{fi:.3f}|B:{avg_b:.1e}"

                res_rows.append(res_entry)
                audit_rows.append(aud_entry)
            
            st.session_state.final_tables = (pd.DataFrame(res_rows), pd.DataFrame(audit_rows))

    if st.session_state.final_tables:
        res_df, aud_df = st.session_state.final_tables
        st.write("### Table 2: Final Results"); st.dataframe(res_df)
        st.write("### Table 3: Audit Log"); st.dataframe(aud_df)
        
        csv_buffer = io.StringIO()
        res_df.to_csv(csv_buffer, index=False)
        st.download_button("📥 Download Final Results", csv_buffer.getvalue(), "ElementaQ_v3.csv", "text/csv")
