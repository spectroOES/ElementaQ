import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ v4.5", layout="wide")

# --- 1. CORE MATH & QA UTILITIES ---
def extract_numeric(val):
    if pd.isna(val) or val == "n/a": return 0.0
    s = str(val).replace('!', '').replace('<', '').strip()
    try: return float(s)
    except: return 0.0

def get_metadata(label, row_type):
    lb, tp = str(label), str(row_type).upper()
    target, dilution = None, 1.0
    dil_match = re.search(r'_dil(\d+\.?\d*)$', lb)
    if dil_match: dilution = float(dil_match.group(1))
    if tp in ['ICV', 'CCV']:
        target_match = re.search(r'_(\d+\.?\d*)(?:_dil\d+)?$', lb)
        if target_match: target = float(target_match.group(1))
    return target, dilution

# --- 2. UI LAYOUT ---
st.sidebar.header("⚙️ Controls")
rsd_low = st.sidebar.slider("Yellow Flag (!)", 1.0, 15.0, 6.0)
rsd_high = st.sidebar.slider("Red Flag (!!)", 1.0, 25.0, 10.0)
inc_threshold = st.sidebar.slider("Inclusion Threshold (%)", 10, 100, 50)
match_window = st.sidebar.slider("Match Window (%)", 5.0, 500.0, (20.0, 200.0))
mismatch_act = st.sidebar.selectbox("Mismatch Action", ["Warn only", "Skip Correction"])

st.title("🧪 ElementaQ v4.5")
uploaded_file = st.file_uploader("Upload Qtegra CSV", type="csv")

if uploaded_file:
    raw_data = pd.read_csv(uploaded_file)
    raw_data.columns = raw_data.columns.str.strip()
    elements = [c for c in raw_data.columns if c not in ['Category', 'Label', 'Type']]

    # --- PHASE 1 ---
    if st.button("📊 Run Phase 1: Filter"):
        p1_rows = []
        for i in range(0, len(raw_data) - (len(raw_data) % 4), 4):
            block = raw_data.iloc[i : i + 4]
            name = str(block['Label'].iloc[0]).strip()
            tp = str(block['Type'].iloc[0]).strip().upper() if 'Type' in block.columns else "S"
            target, dil = get_metadata(name, tp)
            row = {'Index': (i//4)+1, 'Label': name, 'Type': tp, '_target': target, 'Dilution': dil}
            for el in elements:
                try:
                    c = block['Category'].str.lower()
                    avg, rsd = block[c.contains('average')][el].values[0], float(block[c.contains('rsd')][el].values[0])
                    mql, sd = float(block[c.contains('mql')][el].values[0]), float(block[c.contains('sd')][el].values[0])
                    if "<LQ" in str(avg) or float(avg) < mql:
                        row[el] = f"<{round(abs(sd * 10), 4)}" # QA: Fixed negative LOQ
                    else:
                        flag = "!!" if rsd > rsd_high else ("!" if rsd > rsd_low else "")
                        row[el] = f"{float(avg)}{flag}"
                except: row[el] = "n/a"
            p1_rows.append(row)
        st.session_state.p1_df = pd.DataFrame(p1_rows)

    if 'p1_df' in st.session_state:
        st.subheader("Table 1: Primary Filtered")
        st.dataframe(st.session_state.p1_df.drop(columns=['_target']))

        # --- PHASE 2 ---
        if st.button("🚀 Run Phase 2: Metrology"):
            p1 = st.session_state.p1_df.copy()
            p2_rows, p3_rows = [], []
            blks = p1[p1['Type'] == 'BLK']
            avg_b = {el: np.mean([extract_numeric(v) for v in blks[el]]) if not blks.empty else 0.0 for el in elements}

            for _, row in p1.iterrows():
                p2_r, p3_r = {'Index': row['Index'], 'Label': row['Label']}, {'Index': row['Index'], 'Label': row['Label']}
                for el in elements:
                    raw_n = extract_numeric(row[el])
                    f, note = 1.0, ""
                    ccvs = p1[(p1['Type'] == 'CCV') & (p1['_target'].notnull())]
                    if not ccvs.empty:
                        valid = [c for _, c in ccvs.iterrows() if extract_numeric(c[el]) >= (c['_target'] * inc_threshold / 100)]
                        if valid:
                            best = min(valid, key=lambda x: abs(x['Index'] - row['Index']))
                            f = best['_target'] / extract_numeric(best[el])
                            m_pc = (raw_n / best['_target']) * 100 if best['_target'] > 0 else 0
                            if not (match_window[0] <= m_pc <= match_window[1]):
                                if mismatch_act == "Skip Correction": f = 1.0; note = " (NoCorr)"
                                else: note = " (!Range)"
                    
                    v_drift = raw_n * f
                    v_net = v_drift - (avg_b[el] if row['Type'] == 'S' else 0.0)
                    # QA: If LOQ, we don't subtract blank from the boundary, we scale the boundary
                    is_loq = "<" in str(row[el])
                    res_val = abs(raw_n * row['Dilution']) if is_loq else (v_net * row['Dilution'])
                    
                    p2_r[el] = f"{'<' if is_loq else ''}{res_val:.4f}{note}"
                    p3_r[el] = f"f:{f:.2f} B:{avg_b[el]:.1e}"
                p2_rows.append(p2_r); p3_rows.append(p3_r)
            
            st.session_state.p2_df, st.session_state.p3_df = pd.DataFrame(p2_rows), pd.DataFrame(p3_rows)

        if 'p2_df' in st.session_state:
            st.subheader("Table 2: Final Calculated Results")
            st.dataframe(st.session_state.p2_df)
            st.subheader("Table 3: Audit Trail (Drift & Blanks)")
            st.dataframe(st.session_state.p3_df)
            
            # Export all in one CSV with separators or separate downloads
            buf = io.StringIO()
            st.session_state.p2_df.to_csv(buf, index=False)
            st.download_button("📥 Download Final Report (Table 2)", buf.getvalue(), "Report.csv", "text/csv")
