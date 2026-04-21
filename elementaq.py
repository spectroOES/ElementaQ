import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ v4.4", layout="wide")

# --- 1. CORE MATH ENGINE ---
def extract_numeric(val):
    """QA Note: Must handle '1.23!!', '<0.005', and 'n/a' without failing."""
    if pd.isna(val) or val == "n/a": return 0.0
    # Remove flags but keep scientific notation
    s = str(val).replace('!', '').replace('<', '').strip()
    try: return float(s)
    except: return 0.0

def get_metadata(label, row_type):
    """QA Note: Strict adherence to Name_XX and _dilXX syntax."""
    lb = str(label)
    tp = str(row_type).upper()
    target, dilution = None, 1.0
    
    # Extract dilution (e.g., _dil10)
    dil_match = re.search(r'_dil(\d+\.?\d*)$', lb)
    if dil_match: dilution = float(dil_match.group(1))
    
    # Extract target for CCV/ICV (e.g., Mix_10)
    if tp in ['ICV', 'CCV']:
        target_match = re.search(r'_(\d+\.?\d*)(?:_dil\d+)?$', lb)
        if target_match: target = float(target_match.group(1))
    return target, dilution

# --- 2. UI LAYOUT ---
st.sidebar.header("⚙️ RSD Control (Phase 1)")
rsd_low = st.sidebar.slider("Yellow Flag (!)", 1.0, 15.0, 6.0)
rsd_high = st.sidebar.slider("Red Flag (!!)", 1.0, 25.0, 10.0)

st.sidebar.header("⚙️ Metrology Control (Phase 2)")
inc_threshold = st.sidebar.slider("Inclusion Threshold (%)", 10, 100, 50)
match_min, match_max = st.sidebar.slider("Match Window (%)", 5.0, 500.0, (20.0, 200.0))
mismatch_act = st.sidebar.selectbox("Mismatch Action", ["Warn only", "Skip Correction"])

st.title("🧪 ElementaQ v4.4")
uploaded_file = st.file_uploader("Upload Qtegra CSV", type="csv")

if uploaded_file:
    raw_data = pd.read_csv(uploaded_file)
    raw_data.columns = raw_data.columns.str.strip()
    # Filter elements: exclude metadata columns
    elements = [c for c in raw_data.columns if c not in ['Category', 'Label', 'Type']]

    # --- PHASE 1: FILTERING ---
    if st.button("📊 Run Phase 1: Filter & Flag"):
        p1_rows = []
        # Process in blocks of 4 (Avg, SD, RSD, MQL)
        for i in range(0, len(raw_data) - (len(raw_data) % 4), 4):
            block = raw_data.iloc[i : i + 4]
            name = str(block['Label'].iloc[0]).strip()
            tp = str(block['Type'].iloc[0]).strip().upper() if 'Type' in block.columns else "S"
            target, dil = get_metadata(name, tp)
            
            row = {'Index': (i//4)+1, 'Label': name, 'Type': tp, '_target': target, 'Dilution': dil}
            
            for el in elements:
                try:
                    # Case-insensitive category matching
                    c_low = block['Category'].str.lower()
                    avg = block[c_low.str.contains('average')][el].values[0]
                    rsd = float(block[c_low.str.contains('rsd')][el].values[0])
                    mql = float(block[c_low.str.contains('mql')][el].values[0])
                    sd  = float(block[c_low.str.contains('sd')][el].values[0])
                    
                    # LOQ Logic
                    if "<LQ" in str(avg) or float(avg) < mql:
                        row[el] = f"<{round(sd * 10, 4)}"
                    else:
                        val = float(avg)
                        flag = "!!" if rsd > rsd_high else ("!" if rsd > rsd_low else "")
                        row[el] = f"{val}{flag}"
                except: row[el] = "n/a"
            p1_rows.append(row)
        st.session_state.p1_df = pd.DataFrame(p1_rows)

    if 'p1_df' in st.session_state:
        st.subheader("Table 1: Primary Filtered Data")
        st.caption("Internal targets are used for calculation but hidden here.")
        st.dataframe(st.session_state.p1_df.drop(columns=['_target']))

        # --- PHASE 2: CALCULATIONS ---
        if st.button("🚀 Run Phase 2: Full Metrology"):
            p1 = st.session_state.p1_df.copy()
            p2_rows = []
            
            # 1. Global Blank Avg (BLK type only)
            blks = p1[p1['Type'] == 'BLK']
            avg_blanks = {el: np.mean([extract_numeric(v) for v in blks[el]]) if not blks.empty else 0.0 for el in elements}

            # 2. Main Loop
            for _, row in p1.iterrows():
                p2_row = {'Index': row['Index'], 'Label': row['Label'], 'Type': row['Type']}
                for el in elements:
                    raw_num = extract_numeric(row[el])
                    f_drift = 1.0
                    note = ""
                    
                    # Drift Interpolation Logic
                    ccvs = p1[(p1['Type'] == 'CCV') & (p1['_target'].notnull())]
                    if not ccvs.empty:
                        valid_points = []
                        for _, c in ccvs.iterrows():
                            meas = extract_numeric(c[el])
                            # Inclusion Check: 50% threshold
                            if meas >= (c['_target'] * inc_threshold / 100):
                                valid_points.append({'idx': c['Index'], 'f': c['_target']/meas, 't': c['_target']})
                        
                        if valid_points:
                            # Nearest Neighbor match
                            best = min(valid_points, key=lambda x: abs(x['idx'] - row['Index']))
                            f_drift = best['f']
                            
                            # Concentration Match check
                            m_ratio = (raw_num / best['t']) * 100 if best['t'] > 0 else 0
                            if not (match_min <= m_ratio <= match_max):
                                if mismatch_act == "Skip Correction":
                                    f_drift = 1.0
                                    note = " (NoCorr)"
                                else: note = " (!Range)"

                    # 3. Metrological Sequence
                    # Step 3: Drift -> Step 4: Blank (S only) -> Step 5: Dilution
                    v_drift = raw_num * f_drift
                    v_net = v_drift - (avg_blanks[el] if row['Type'] == 'S' else 0.0)
                    v_final = v_net * row['Dilution']
                    
                    # Formatting: Preserve < for LOQ but scale the value
                    prefix = "<" if "<" in str(row[el]) else ""
                    p2_row[el] = f"{prefix}{v_final:.4f}{note}"
                
                p2_rows.append(p2_row)
            
            st.session_state.p2_df = pd.DataFrame(p2_rows)
            st.subheader("Table 2: Final Metrological Results")
            st.dataframe(st.session_state.p2_df)
            
            # Export functionality
            csv_buff = io.StringIO()
            st.session_state.p2_df.to_csv(csv_buff, index=False)
            st.download_button("📥 Export Final Report", csv_buff.getvalue(), "ElementaQ_Final.csv", "text/csv")
