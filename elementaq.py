import streamlit as st
import pandas as pd
import numpy as np
import re
import io

# 1. Page Configuration
st.set_page_config(page_title="ElementaQ v2.8", layout="wide", page_icon="🧪")

# 2. THE STABLE SIDEBAR (Only the 2 original sliders)
# These are placed here to remain visible regardless of file upload state.
st.sidebar.header("Global Control Settings")
rsd_limit = st.sidebar.slider("RSD Threshold (%)", 1.0, 25.0, 10.0)
drift_threshold = st.sidebar.slider("CCV Drift Limit (%)", 1.0, 20.0, 10.0)

# 3. Core Analytical Functions
def safe_parse(text):
    """Robust parser to prevent ValueError."""
    if pd.isna(text) or text == "": return 0.0
    # Removes '<', '!', and any non-numeric characters except scientific notation
    s = str(text).split('<')[-1].split('!')[0].strip()
    clean = re.sub(r'[^0-9.eE-]', '', s)
    try: return float(clean) if clean else 0.0
    except: return 0.0

def parse_metadata(label):
    """Extracts Target concentration and Dilution Factor from Label."""
    lb = str(label)
    target = re.search(r'[_.]?(\d+\.?\d*)$', lb)
    dilution = re.search(r'_dil(\d+\.?\d*)', lb)
    return float(target.group(1)) if target else None, float(dilution.group(1)) if dilution else 1.0

def format_output(v, is_lq=False):
    """Scientific formatting for trace elements."""
    prefix = "<" if is_lq else ""
    val = max(abs(v), 1e-12) if is_lq else v
    return f"{prefix}{val:.4e}" if 0 < abs(val) < 1e-6 else f"{prefix}{val:.9f}"

# 4. Persistence Management
if 'st1_data' not in st.session_state: st.session_state.st1_data = None
if 'final_tables' not in st.session_state: st.session_state.final_tables = None

# 5. Main Application UI
st.title("🧪 ElementaQ: Analytical Audit Suite (v2.8)")
st.caption("PhD-Level Metrology | Clean UI | Master Equation Logic")

uploaded_file = st.file_uploader("Upload Qtegra CSV File", type="csv")

if uploaded_file:
    raw = pd.read_csv(uploaded_file)
    raw.columns = raw.columns.str.strip()
    elements = [c for c in raw.columns if c not in ['Category', 'Label', 'Type']]

    # STEP 1: INDEXING & RSD CHECK
    if st.button("📊 Step 1: Process Structure & RSD"):
        processed = []
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
                    # Filter valid CCVs within the defined limit
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

                    # Hybrid Drift Model
                    if len(ccv_pool) == 1:
                        f_i = ccv_pool[0]['target'] / ccv_pool[0]['meas']
                    else:
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
                    
                    # Master Equation Sequential Logic
                    raw_v = safe_parse(row[el])
                    stype = row['Type']
                    
                    # 1. Apply Drift Factor (fi)
                    f_applied = f_i if stype in ['S', 'BLK'] else 1.0
                    c_drifted = raw_v * f_applied

                    # 2. Subtract Analytical Blank
                    blanks = p1[p1['Type'] == 'BLK']
                    drift_blanks = [safe_parse(b[el]) * (ccv_pool[0]['target'] / ccv_pool[0]['meas']) for _, b in blanks.iterrows()]
                    avg_blank = np.mean(drift_blanks) if drift_blanks else 0.0
                    b_applied = avg_blank if stype in ['S', 'MBB'] else 0.0
                    c_net = c_drifted - b_applied

                    # 3. Final Dilution Scaling
                    final_v = c_net * row['Dilution']
                    
                    res_entry[el] = format_output(final_v, '<' in str(row[el]))
                    aud_entry[el] = f"f:{f_applied:.4f}|B:{b_applied:.1e}"

                res_rows.append(res_entry)
                audit_rows.append(aud_entry)
            
            st.session_state.final_tables = (pd.DataFrame(res_rows), pd.DataFrame(audit_rows))

    if st.session_state.final_tables:
        res_df, aud_df = st.session_state.final_tables
        st.write("### Table 2: Final Results")
        st.dataframe(res_df)
        st.write("### Table 3: Metrological Audit Log")
        st.dataframe(aud_df)

        out = io.StringIO()
        out.write("ELEMENTAQ v2.8 FINAL REPORT\n" + "="*30 + "\n")
        out.write("\n1. INPUT\n"); st.session_state.st1_data.to_csv(out, index=False)
        out.write("\n2. RESULTS\n"); res_df.to_csv(out, index=False)
        out.write("\n3. AUDIT\n"); aud_df.to_csv(out, index=False)
        st.download_button("📥 Download Report (CSV)", out.getvalue(), "ElementaQ_v28.csv", "text/csv")
