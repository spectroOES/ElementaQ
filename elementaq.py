import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ v5.2", layout="wide")

# --- INITIALIZE SESSION STATE ---
if 'p1_df' not in st.session_state: st.session_state.p1_df = None
if 'p2_df' not in st.session_state: st.session_state.p2_df = None

# --- UTILITIES ---
def get_val(val):
    if pd.isna(val) or val == "n/a": return 0.0
    s = str(val).replace('!', '').replace('<', '').strip()
    try: return float(s)
    except: return 0.0

def parse_meta(label, r_type):
    lb, tp = str(label), str(r_type).upper()
    target, dil = None, 1.0
    d_m = re.search(r'_dil(\d+\.?\d*)$', lb)
    if d_m: dil = float(d_m.group(1))
    if tp in ['ICV', 'CCV']:
        # Improved target extraction: search for numeric value after underscore
        t_m = re.search(r'_(\d+\.?\d*)(?:_dil\d+)?$', lb)
        if t_m: target = float(t_m.group(1))
    return target, dil

# --- SIDEBAR ---
st.sidebar.header("Global Parameters")
r_l = st.sidebar.slider("Yellow Flag (!)", 1.0, 15.0, 6.0)
r_h = st.sidebar.slider("Red Flag (!!)", 1.0, 25.0, 10.0)
inc_t = st.sidebar.slider("Inclusion Threshold (%)", 10, 100, 50)

st.title("🧪 ElementaQ v5.2")
file = st.file_uploader("Upload Laboratory CSV", type="csv")

if file:
    df_raw = pd.read_csv(file)
    df_raw.columns = df_raw.columns.str.strip()
    elements = [c for c in df_raw.columns if c not in ['Category', 'Label', 'Type']]

    # --- PHASE 1 ---
    if st.button("📊 Step 1: Run Filter"):
        rows = []
        valid_limit = len(df_raw) - (len(df_raw) % 4)
        for i in range(0, valid_limit, 4):
            block = df_raw.iloc[i : i+4]
            name, tp = str(block['Label'].iloc[0]).strip(), str(block['Type'].iloc[0]).upper()
            tgt, dil = parse_meta(name, tp)
            r = {'Index': (i//4)+1, 'Label': name, 'Type': tp, '_t': tgt, 'Dilution': dil}
            for el in elements:
                try:
                    v_avg = block[el].iloc[0]
                    v_sd  = float(block[el].iloc[1])
                    v_rsd = float(block[el].iloc[2])
                    v_mql = float(block[el].iloc[3])
                    if "<LQ" in str(v_avg) or float(v_avg) < v_mql:
                        # QA: Removed rounding here to preserve precision <0.000073651
                        r[el] = f"<{abs(v_sd * 10)}"
                    else:
                        f = "!!" if v_rsd > r_h else ("!" if v_rsd > r_l else "")
                        r[el] = f"{float(v_avg)}{f}"
                except: r[el] = "n/a"
            rows.append(r)
        st.session_state.p1_df = pd.DataFrame(rows)

    if st.session_state.p1_df is not None:
        st.subheader("Table 1: Primary Filtered")
        st.dataframe(st.session_state.p1_df.drop(columns=['_t']))

        # --- PHASE 2 ---
        if st.button("🚀 Step 2: Run Metrology"):
            p1 = st.session_state.p1_df.copy()
            res2, res3 = [], []
            blks = p1[p1['Type'] == 'BLK']
            avg_b = {el: np.mean([get_val(v) for v in blks[el]]) if not blks.empty else 0.0 for el in elements}

            for _, row in p1.iterrows():
                r2, r3 = {'Index': row['Index'], 'Label': row['Label'], 'Type': row['Type']}, {'Index': row['Index'], 'Label': row['Label']}
                for el in elements:
                    val_raw = get_val(row[el])
                    f_drift = 1.0
                    
                    # Improved Drift Matching
                    ccvs = p1[(p1['Type'] == 'CCV') & (p1['_t'].notnull())]
                    if not ccvs.empty:
                        # Filter valid standards based on inclusion threshold
                        pts = []
                        for _, c in ccvs.iterrows():
                            meas = get_val(c[el])
                            if meas >= (c['_t'] * inc_t / 100):
                                pts.append({'idx': c['Index'], 'f': c['_t']/meas})
                        
                        if pts:
                            # Nearest CCV logic
                            best = min(pts, key=lambda x: abs(x['idx'] - row['Index']))
                            f_drift = best['f']
                    
                    v_drift = val_raw * f_drift
                    v_net = v_drift - (avg_b[el] if row['Type'] == 'S' else 0.0)
                    is_loq = "<" in str(row[el])
                    
                    # Final result Calculation
                    res_val = max(0.0, val_raw * row['Dilution']) if is_loq else max(0.0, v_net * row['Dilution'])
                    
                    # Formatting with scientific notation for very small LOQs if needed
                    if res_val < 0.0001 and res_val > 0:
                        formatted = f"{res_val:.8f}".rstrip('0').rstrip('.')
                    else:
                        formatted = f"{res_val:.4f}"
                        
                    r2[el] = f"{'<' if is_loq else ''}{formatted}"
                    r3[el] = f"f:{f_drift:.4f} B:{avg_b[el]:.2e}"
                res2.append(r2); res3.append(r3)
            st.session_state.p2_df = pd.DataFrame(res2)
            st.session_state.p3_df = pd.DataFrame(res3)

        if st.session_state.p2_df is not None:
            st.subheader("Table 2: Final Results")
            st.dataframe(st.session_state.p2_df)
            st.subheader("Table 3: Audit Trail")
            st.dataframe(st.session_state.p3_df)
            
            output = io.StringIO()
            output.write("TABLE 2: FINAL CALCULATED RESULTS\n")
            st.session_state.p2_df.to_csv(output, index=False)
            output.write("\n\nTABLE 3: AUDIT TRAIL\n")
            st.session_state.p3_df.to_csv(output, index=False)
            output.write("\n\nTABLE 1: PRIMARY DATA\n")
            st.session_state.p1_df.drop(columns=['_t']).to_csv(output, index=False)
            
            st.download_button(
                label="📥 Download Full Report (3 Tables)",
                data=output.getvalue().encode('utf-8-sig'),
                file_name="ElementaQ_Report_v5.2.csv",
                mime="text/csv"
            )
