import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ v4.2", layout="wide")

# --- 1. UI SIDEBAR ---
st.sidebar.header("⚙️ Phase 1: RSD Limits")
rsd_low = st.sidebar.slider("Yellow Flag (!)", 1.0, 15.0, 6.0, 0.5)
rsd_high = st.sidebar.slider("Red Flag (!!)", 1.0, 25.0, 10.0, 0.5)

st.sidebar.header("⚙️ Phase 2: Metrological Settings")
inc_threshold = st.sidebar.slider("Inclusion Threshold (% of Target)", 10.0, 100.0, 50.0)
match_range = st.sidebar.slider("Match Window (%)", 5.0, 500.0, (20.0, 200.0))
mismatch_action = st.sidebar.selectbox("Action on Mismatch", ["Warn only", "Skip Correction"])

# --- 2. CORE UTILITIES ---
def clean_to_float(val):
    """Safely converts flagged strings like '<0.005' or '1.2!!' to float for math."""
    if pd.isna(val) or val == "n/a": return 0.0
    s = str(val).replace('!', '').replace('<', '').strip()
    try: return float(s)
    except: return 0.0

def parse_metadata(label, row_type):
    """Extracts target from _XX and dilution from _dilXX"""
    lb = str(label)
    tp = str(row_type).upper()
    target, dilution = None, 1.0
    
    dil_match = re.search(r'_dil(\d+\.?\d*)$', lb)
    if dil_match: dilution = float(dil_match.group(1))
    
    if tp in ['ICV', 'CCV']:
        target_match = re.search(r'_(\d+\.?\d*)(?:_dil\d+)?$', lb)
        if target_match: target = float(target_match.group(1))
    
    return target, dilution

# --- 3. MAIN INTERFACE ---
st.title("🧪 ElementaQ v4.2")

uploaded_file = st.file_uploader("Upload your source CSV file", type="csv")

if uploaded_file:
    df_raw = pd.read_csv(uploaded_file)
    df_raw.columns = df_raw.columns.str.strip()
    elements = [col for col in df_raw.columns if col not in ['Category', 'Label', 'Type']]

    # PHASE 1: YOUR ORIGINAL LOGIC
    if st.button("📊 Phase 1: Filter & Primary Table"):
        final_results = []
        valid_rows = len(df_raw) - (len(df_raw) % 4)

        for i in range(0, valid_rows, 4):
            block = df_raw.iloc[i : i + 4].copy()
            block['Category'] = block['Category'].astype(str).str.strip()
            sample_name = str(block['Label'].iloc[0]).strip()
            row_type = str(block['Type'].iloc[0]).strip().upper() if 'Type' in block.columns else "S"
            
            target, dil = parse_metadata(sample_name, row_type)
            new_row = {'Index': (i//4)+1, 'Label': sample_name, 'Type': row_type, 'Target': target, 'Dilution': dil}
            
            for el in elements:
                try:
                    avg_val = block[block['Category'].str.contains('average', case=False, na=False)][el].values[0]
                    sd_val  = float(block[block['Category'].str.contains('SD', case=False, na=False)][el].values[0])
                    rsd_val = float(block[block['Category'].str.contains('RSD', case=False, na=False)][el].values[0])
                    mql_val = float(block[block['Category'].str.contains('MQL', case=False, na=False)][el].values[0])
                    
                    if "<LQ" in str(avg_val) or float(avg_val) < mql_val:
                        res = f"<{round(sd_val * 10, 3)}"
                    else:
                        num_avg = float(avg_val)
                        if rsd_val > rsd_high: res = f"{num_avg}!!"
                        elif rsd_val > rsd_low: res = f"{num_avg}!"
                        else: res = str(num_avg)
                    new_row[el] = res
                except:
                    new_row[el] = "n/a"
            final_results.append(new_row)
        
        st.session_state.table1 = pd.DataFrame(final_results)

    if 'table1' in st.session_state:
        st.subheader("Table 1: Primary Filtered Data")
        st.dataframe(st.session_state.table1)

        # PHASE 2: METROLOGICAL WORKFLOW (FIXED ERROR)
        if st.button("🚀 Phase 2: Run Full Metrological Workflow"):
            t1 = st.session_state.table1.copy()
            res_list, audit_list = [], []
            
            # Step A: Pre-calculate Blanks (BLK only) with float conversion
            blks = t1[t1['Type'] == 'BLK']
            avg_blanks = {}
            for el in elements:
                if not blks.empty:
                    # Fix: use clean_to_float to avoid ValueError
                    vals = [clean_to_float(v) for v in blks[el] if v != "n/a"]
                    avg_blanks[el] = np.mean(vals) if vals else 0.0
                else:
                    avg_blanks[el] = 0.0

            # Step B: Main Processing Loop
            for _, row in t1.iterrows():
                r_row = {'Index': row['Index'], 'Label': row['Label'], 'Type': row['Type']}
                a_row = {'Index': row['Index'], 'Label': row['Label']}
                
                for el in elements:
                    current_raw = clean_to_float(row[el])
                    f_factor = 1.0
                    status = ""

                    # 1. Search for CCV drift
                    ccvs = t1[(t1['Type'] == 'CCV') & (t1['Target'].notnull())]
                    if not ccvs.empty:
                        valid_ccvs = []
                        for _, c in ccvs.iterrows():
                            c_meas = clean_to_float(c[el])
                            # Inclusion threshold check
                            if c_meas >= (c['Target'] * inc_threshold / 100):
                                valid_ccvs.append({'idx': c['Index'], 'f': c['Target']/c_meas, 'target': c['Target']})
                        
                        if valid_ccvs:
                            best = min(valid_ccvs, key=lambda x: abs(x['idx'] - row['Index']))
                            f_factor = best['f']
                            
                            # Concentration Match check
                            match_pc = (current_raw / best['target']) * 100 if best['target'] > 0 else 0
                            if not (match_range[0] <= match_pc <= match_range[1]):
                                if mismatch_action == "Skip Correction":
                                    f_factor = 1.0
                                    status = "⚠️ Skip(Range)"
                                else:
                                    status = "⚠️ RangeMismatch"

                    # 2. Calculation Sequence
                    c_drift = current_raw * f_factor
                    c_net = c_drift - (avg_blanks[el] if row['Type'] == 'S' else 0.0)
                    final = c_net * row['Dilution']
                    
                    # Formatting
                    fmt = f"{final:.4f}"
                    if '<' in str(row[el]): fmt = f"<{fmt}"
                    
                    r_row[el] = f"{fmt} {status}".strip()
                    a_row[el] = f"f:{f_factor:.2f}|B:{avg_blanks[el]:.1e}"
                
                res_list.append(r_row)
                audit_list.append(a_row)

            st.session_state.table2 = pd.DataFrame(res_list)
            st.subheader("Table 2: Final Calculated Results")
            st.dataframe(st.session_state.table2)
            
            # Export
            out = io.StringIO()
            st.session_state.table2.to_csv(out, index=False)
            st.download_button("📥 Download Final Report", out.getvalue(), "ElementaQ_v42.csv", "text/csv")
