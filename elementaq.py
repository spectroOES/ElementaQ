import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ v4.9", layout="wide")

# --- CORE UTILITIES ---
def clean_num(val):
    if pd.isna(val) or val == "n/a": return 0.0
    s = str(val).replace('!', '').replace('<', '').strip()
    try: return float(s)
    except: return 0.0

def get_meta(label, row_type):
    lb, tp = str(label), str(row_type).upper()
    target, dilution = None, 1.0
    d_m = re.search(r'_dil(\d+\.?\d*)$', lb)
    if d_m: dilution = float(d_m.group(1))
    if tp in ['ICV', 'CCV']:
        t_m = re.search(r'_(\d+\.?\d*)(?:_dil\d+)?$', lb)
        if t_m: target = float(t_m.group(1))
    return target, dilution

# --- SIDEBAR ---
st.sidebar.header("RSD Settings")
rsd_l = st.sidebar.slider("Yellow Flag (!)", 1.0, 15.0, 6.0, 0.5)
rsd_h = st.sidebar.slider("Red Flag (!!)", 1.0, 25.0, 10.0, 0.5)

st.sidebar.header("Metrology Settings")
inc_t = st.sidebar.slider("Inclusion Threshold (%)", 10, 100, 50)
match_w = st.sidebar.slider("Match Window (%)", 5.0, 500.0, (20.0, 200.0))

st.title("🧪 ElementaQ v4.9")
file = st.file_uploader("Upload CSV", type="csv")

if file:
    df = pd.read_csv(file)
    df.columns = df.columns.str.strip()
    elements = [c for c in df.columns if c not in ['Category', 'Label', 'Type']]

    # --- PHASE 1: TRIGGERED BY BUTTON ONLY ---
    if st.button("📊 Run Phase 1: Filter"):
        p1_rows = []
        valid_rows = len(df) - (len(df) % 4)
        for i in range(0, valid_rows, 4):
            block = df.iloc[i : i + 4]
            name = str(block['Label'].iloc[0]).strip()
            tp = str(block['Type'].iloc[0]).upper() if 'Type' in block.columns else "S"
            target, dil = get_meta(name, tp)
            row = {'Index': (i//4)+1, 'Label': name, 'Type': tp, '_t': target, 'Dilution': dil}
            
            for el in elements:
                try:
                    # Index-based access to avoid n/a errors
                    avg_v = block[el].iloc[0]
                    sd_v  = float(block[el].iloc[1])
                    rsd_v = float(block[el].iloc[2])
                    mql_v = float(block[el].iloc[3])
                    
                    if "<LQ" in str(avg_v) or float(avg_v) < mql_v:
                        # Ensure boundary is positive
                        row[el] = f"<{round(abs(sd_v * 10), 4)}"
                    else:
                        flag = "!!" if rsd_v > rsd_h else ("!" if rsd_v > rsd_l else "")
                        row[el] = f"{float(avg_v)}{flag}"
                except: row[el] = "n/a"
            p1_rows.append(row)
        st.session_state.p1 = pd.DataFrame(p1_rows)

    # --- DISPLAY TABLE 1 ---
    if 'p1' in st.session_state:
        st.subheader("Table 1: Primary Filtered Data")
        st.dataframe(st.session_state.p1.drop(columns=['_t']))

        # --- PHASE 2: TRIGGERED BY BUTTON ONLY ---
        if st.button("🚀 Run Phase 2: Metrology"):
            p1 = st.session_state.p1.copy()
            p2_rows, p3_rows = [], []
            
            # Global Blank calculation (BLK only)
            blks = p1[p1['Type'] == 'BLK']
            avg_b = {el: np.mean([clean_num(v) for v in blks[el]]) if not blks.empty else 0.0 for el in elements}

            for _, r in p1.iterrows():
                r2, r3 = {'Index': r['Index'], 'Label': r['Label'], 'Type': r['Type']}, {'Index': r['Index'], 'Label': r['Label']}
                for el in elements:
                    raw_v = clean_num(r[el])
                    f = 1.0
                    
                    # CCV Drift Logic
                    ccvs = p1[(p1['Type'] == 'CCV') & (p1['_t'].notnull())]
                    if not ccvs.empty:
                        pts = [c for _, c in ccvs.iterrows() if clean_num(c[el]) >= (c['_t'] * inc_t / 100)]
                        if pts:
                            best = min(pts, key=lambda x: abs(x['Index'] - r['Index']))
                            f = best['_t'] / clean_num(best[el])
                    
                    # Metrology math sequence
                    v_drift = raw_v * f
                    v_net = v_drift - (avg_b[el] if r['Type'] == 'S' else 0.0)
                    is_loq = "<" in str(r[el])
                    
                    # Final value: ensure positive
                    final_v = max(0.0, raw_v * r['Dilution']) if is_loq else max(0.0, v_net * r['Dilution'])
                    
                    r2[el] = f"{'<' if is_loq else ''}{final_v:.4f}"
                    r3[el] = f"f:{f:.2f} B:{avg_b[el]:.2e}"
                p2_rows.append(r2); p3_res = p3_rows.append(r3)
            
            st.session_state.p2 = pd.DataFrame(p2_rows)
            st.session_state.p3 = pd.DataFrame(p3_rows)

        # --- DISPLAY RESULTS ---
        if 'p2' in st.session_state:
            st.subheader("Table 2: Final Calculated Results")
            st.dataframe(st.session_state.p2)
            
            if 'p3' in st.session_state:
                st.subheader("Table 3: Audit Trail (Drift & Blanks)")
                st.dataframe(st.session_state.p3)
            
            report = st.session_state.p2.to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 Download Final CSV", report, "ElementaQ_Report.csv", "text/csv")
