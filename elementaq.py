import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ v5.5", layout="wide")

# --- CORE MATH UTILITIES ---
def clean_num(val):
    if pd.isna(val) or val == "n/a": return 0.0
    s = str(val).replace('!', '').replace('<', '').strip()
    try: return float(s)
    except: return 0.0

def get_meta(label, row_type):
    lb, tp = str(label), str(row_type).upper()
    target, dil = None, 1.0
    d_m = re.search(r'_dil(\d+\.?\d*)$', lb)
    if d_m: dil = float(d_m.group(1))
    if tp in ['ICV', 'CCV']:
        t_m = re.search(r'_(\d+\.?\d*)(?:_dil\d+)?$', lb)
        if t_m: target = float(t_m.group(1))
    return target, dil

# --- SIDEBAR ---
st.sidebar.header("Metrology Config")
r_l = st.sidebar.slider("Yellow Flag (!)", 1.0, 15.0, 6.0)
r_h = st.sidebar.slider("Red Flag (!!)", 1.0, 25.0, 10.0)
inc_t = st.sidebar.slider("CCV Inclusion Threshold (%)", 10, 100, 50)

st.title("🧪 ElementaQ v5.5 (Hybrid Drift)")
file = st.file_uploader("Upload Laboratory CSV", type="csv")

if file:
    df_raw = pd.read_csv(file)
    df_raw.columns = df_raw.columns.str.strip()
    elements = [c for c in df_raw.columns if c not in ['Category', 'Label', 'Type']]

    # --- PHASE 1 ---
    if st.button("📊 Step 1: Filter & Precision LOQ"):
        p1_rows = []
        valid_limit = len(df_raw) - (len(df_raw) % 4)
        for i in range(0, valid_limit, 4):
            block = df_raw.iloc[i : i+4]
            name, tp = str(block['Label'].iloc[0]).strip(), str(block['Type'].iloc[0]).upper()
            tgt, dil = get_meta(name, tp)
            r = {'Index': (i//4)+1, 'Label': name, 'Type': tp, '_t': tgt, 'Dilution': dil}
            for el in elements:
                try:
                    v_avg, v_sd = block[el].iloc[0], float(block[el].iloc[1])
                    v_rsd, v_mql = float(block[el].iloc[2]), float(block[el].iloc[3])
                    if "<LQ" in str(v_avg) or float(v_avg) < v_mql:
                        r[el] = f"<{abs(v_sd * 10)}" # STRICTOR PRECISION
                    else:
                        flag = "!!" if v_rsd > r_h else ("!" if v_rsd > r_l else "")
                        r[el] = f"{float(v_avg)}{flag}"
                except: r[el] = "n/a"
            p1_rows.append(r)
        st.session_state.p1_df = pd.DataFrame(p1_rows)

    if 'p1_df' in st.session_state and st.session_state.p1_df is not None:
        st.subheader("Table 1: Primary Filtered Data")
        st.dataframe(st.session_state.p1_df.drop(columns=['_t']))

        # --- PHASE 2 ---
        if st.button("🚀 Step 2: Run Hybrid Metrology"):
            p1 = st.session_state.p1_df.copy()
            res2, res3 = [], []
            
            # Blank Correction (Average of all BLKs)
            blks = p1[p1['Type'] == 'BLK']
            avg_b = {el: np.mean([clean_num(v) for v in blks[el]]) if not blks.empty else 0.0 for el in elements}

            # Initialize results storage
            for idx, row in p1.iterrows():
                res2.append({'Index': row['Index'], 'Label': row['Label'], 'Type': row['Type']})
                res3.append({'Index': row['Index'], 'Label': row['Label']})

            for el in elements:
                # 1. Identify all valid standards for this element
                ccvs = p1[(p1['Type'] == 'CCV') & (p1['_t'].notnull())]
                v_ccvs = []
                for _, c in ccvs.iterrows():
                    meas = clean_num(c[el])
                    if meas >= (c['_t'] * inc_t / 100):
                        v_ccvs.append({'idx': c['Index'], 'f': c['_t'] / meas})
                
                # 2. Process each sample with Hybrid Logic
                for idx, row in p1.iterrows():
                    cur_idx = row['Index']
                    f_drift = 1.0
                    
                    if v_ccvs:
                        before = [v for v in v_ccvs if v['idx'] <= cur_idx]
                        after = [v for v in v_ccvs if v['idx'] > cur_idx]
                        
                        if before and after:
                            # Two-point Linear Interpolation
                            c1, c2 = before[-1], after[0]
                            f_drift = c1['f'] + (c2['f'] - c1['f']) * (cur_idx - c1['idx']) / (c2['idx'] - c1['idx'])
                        elif before:
                            # Use last available (Single standard or post-CCV sample)
                            f_drift = before[-1]['f']
                        elif after:
                            # Use first available (Pre-CCV sample)
                            f_drift = after[0]['f']

                    # 3. Apply Correction
                    raw_val = clean_num(row[el])
                    v_drift = raw_val * f_drift
                    v_net = v_drift - (avg_b[el] if row['Type'] == 'S' else 0.0)
                    is_loq = "<" in str(row[el])
                    
                    # 4. Final Calculation (No negative values)
                    # For LOQ rows, we just scale the detection limit by dilution
                    final_v = max(0.0, raw_val * row['Dilution']) if is_loq else max(0.0, v_net * row['Dilution'])
                    
                    # Formatting logic for the protocol
                    if 0 < final_v < 0.0001:
                        f_str = f"{final_v:.10f}".rstrip('0').rstrip('.')
                    else:
                        f_str = f"{final_v:.6f}"
                        
                    res2[idx][el] = f"{'<' if is_loq else ''}{f_str}"
                    res3[idx][el] = f"f:{f_drift:.4f} B:{avg_b[el]:.1e}"
            
            st.session_state.p2_df = pd.DataFrame(res2)
            st.session_state.p3_df = pd.DataFrame(res3)

        if 'p2_df' in st.session_state:
            st.subheader("Table 2: Final Protocol Results")
            st.dataframe(st.session_state.p2_df)
            st.subheader("Table 3: Audit Trail (Drift & Blanks)")
            st.dataframe(st.session_state.p3_df)
            
            buf = io.StringIO()
            buf.write("SECTION 1: FINAL CALCULATED RESULTS\n")
            st.session_state.p2_df.to_csv(buf, index=False)
            buf.write("\n\nSECTION 2: AUDIT TRAIL (METROLOGY DATA)\n")
            st.session_state.p3_df.to_csv(buf, index=False)
            buf.write("\n\nSECTION 3: PRIMARY FILTERED DATA\n")
            st.session_state.p1_df.drop(columns=['_t']).to_csv(buf, index=False)
            st.download_button("📥 Export Comprehensive Report", buf.getvalue().encode('utf-8-sig'), "ElementaQ_Report_v5.5.csv", "text/csv")
