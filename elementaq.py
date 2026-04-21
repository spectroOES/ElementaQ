import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ v4.1", layout="wide")

# --- 1. UI CONTROL PANEL ---
st.sidebar.header("⚙️ Global Control Settings")
rsd_limit = st.sidebar.slider("RSD Threshold (%)", 1.0, 25.0, 10.0)
cal_tol = st.sidebar.slider("Calibration Tolerance (±%)", 1.0, 30.0, 10.0)
inc_threshold = st.sidebar.slider("Inclusion Threshold (% of Target)", 10.0, 100.0, 50.0)
blank_max_val = st.sidebar.slider("Blank Max Threshold (abs)", 0.0, 1.0, 0.050, step=0.005)

st.sidebar.header("⚖️ Concentration Match")
match_range = st.sidebar.slider("Match Window (%)", 5.0, 500.0, (20.0, 200.0))
mismatch_action = st.sidebar.selectbox("Action on Mismatch", ["Warn only", "Skip Correction"])

# --- 2. CORE LOGIC FUNCTIONS ---
def parse_name(label, row_type):
    """Syntax Option B: Extracts target from _XX and dilution from _dilXX"""
    lb = str(label)
    tp = str(row_type).upper()
    target, dilution = None, 1.0
    
    dil_match = re.search(r'_dil(\d+\.?\d*)$', lb)
    if dil_match: dilution = float(dil_match.group(1))
    
    if tp in ['ICV', 'CCV']:
        target_match = re.search(r'_(\d+\.?\d*)(?:_dil\d+)?$', lb)
        if target_match: target = float(target_match.group(1))
    
    return target, dilution

def process_loq(val_str, factor):
    """Handles <LOQ values by preserving the symbol during math operations"""
    if '<' in str(val_str):
        num = float(val_str.replace('<', ''))
        return f"<{num * factor:.4e}"
    return float(val_str) * factor

# --- 3. MAIN INTERFACE ---
st.title("🧪 ElementaQ v4.1")
uploaded_file = st.file_uploader("Upload Qtegra CSV", type="csv")

if uploaded_file:
    raw = pd.read_csv(uploaded_file)
    raw.columns = [c.strip() for c in raw.columns]
    elements = [c for c in raw.columns if c not in ['Category', 'Label', 'Type']]

    # PHASE 1: PRIMARY FILTERED
    if st.button("📊 Phase 1: Filter & Index"):
        rows = []
        for i in range(0, len(raw) - (len(raw)%4), 4):
            block = raw.iloc[i:i+4]
            lbl = str(block['Label'].iloc[0]).strip()
            tp = str(block['Type'].iloc[0]).strip().upper() 
            
            target, dil = parse_name(lbl, tp)
            entry = {'Index': (i//4)+1, 'Label': lbl, 'Type': tp, 'Target': target, 'Dilution': dil}
            
            for el in elements:
                avg_val = block[block['Category'].str.contains('average', case=False)][el].values[0]
                rsd_val = float(str(block[block['Category'].str.contains('RSD', case=False)][el].values[0]).replace('%',''))
                
                if rsd_val > rsd_limit: entry[el] = f"{avg_val}!!"
                else: entry[el] = avg_val
            rows.append(entry)
        st.session_state.table1 = pd.DataFrame(rows)

    if 'table1' in st.session_state:
        st.subheader("Table 1: Primary Filtered (Instrument Units)")
        st.dataframe(st.session_state.table1)

        # PHASE 2: FINAL CALCULATED
        if st.button("🚀 Phase 2: Run Full Metrological Workflow"):
            df = st.session_state.table1.copy()
            res_rows, audit_rows = [], []
            
            # Analytical Blank Calculation (BLK)
            blks = df[df['Type'] == 'BLK']
            avg_blanks = {el: np.mean([float(str(v).replace('<','').replace('!!','')) for v in blks[el]]) if not blks.empty else 0.0 for el in elements}

            for _, row in df.iterrows():
                r_row = {'Index': row['Index'], 'Label': row['Label'], 'Type': row['Type']}
                a_row = {'Index': row['Index'], 'Label': row['Label']}
                
                for el in elements:
                    raw_val = float(str(row[el]).replace('<','').replace('!!',''))
                    
                    # 1. CCV Drift Correction Search
                    ccv_points = df[(df['Type'] == 'CCV') & (df['Target'].notnull())]
                    f_factor = 1.0
                    status = ""

                    if not ccv_points.empty:
                        # Inclusion Threshold Logic
                        valid_ccvs = []
                        for _, c in ccv_points.iterrows():
                            c_meas = float(str(c[el]).replace('<','').replace('!!',''))
                            if c_meas >= (c['Target'] * inc_threshold / 100):
                                valid_ccvs.append({'idx': c['Index'], 'f': c['Target']/c_meas, 'target': c['Target']})
                        
                        if valid_ccvs:
                            # Interpolation or nearest neighbor logic
                            best_ccv = min(valid_ccvs, key=lambda x: abs(x['idx'] - row['Index']))
                            f_factor = best_ccv['f']
                            
                            # 2. Concentration Match Logic
                            match_pc = (raw_val / best_ccv['target']) * 100
                            if not (match_range[0] <= match_pc <= match_range[1]):
                                if mismatch_action == "Skip Correction":
                                    f_factor = 1.0
                                    status = "⚠️ Skip(Range)"
                                else:
                                    status = "⚠️ RangeMismatch"

                    # 3. Drift Correction Apply
                    c_drift = raw_val * f_factor
                    
                    # 4. Blank Subtraction (S only)
                    c_net = c_drift - (avg_blanks[el] if row['Type'] == 'S' else 0.0)
                    
                    # 5. Final Calculation with Dilution
                    final_val = c_net * row['Dilution']
                    formatted_res = f"{final_val:.4f}"
                    if '<' in str(row[el]): formatted_res = f"<{formatted_res}"
                    
                    r_row[el] = f"{formatted_res} {status}".strip()
                    a_row[el] = f"f:{f_factor:.3f} | B:{avg_blanks[el]:.2e}"

                res_rows.append(r_row)
                audit_rows.append(a_row)

            st.session_state.table2 = pd.DataFrame(res_rows)
            st.subheader("Table 2: Final Calculated (Report Units)")
            st.dataframe(st.session_state.table2)
            
            csv = st.session_state.table2.to_csv(index=False).encode('utf-8')
            st.download_button("📥 Download Final Report", csv, "ElementaQ_v4.1.csv", "text/csv")
