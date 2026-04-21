import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ v4.6", layout="wide")

# --- 1. CORE UTILITIES ---
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

# --- 2. SIDEBAR CONTROLS ---
st.sidebar.header("⚙️ Global Settings")
rsd_l = st.sidebar.slider("Yellow Flag (!)", 1.0, 15.0, 6.0)
rsd_h = st.sidebar.slider("Red Flag (!!)", 1.0, 25.0, 10.0)
inc_t = st.sidebar.slider("Inclusion Threshold (%)", 10, 100, 50)
match_w = st.sidebar.slider("Match Window (%)", 5.0, 500.0, (20.0, 200.0))

# --- 3. MAIN APP ---
st.title("🧪 ElementaQ v4.6")
file = st.file_uploader("Upload Qtegra CSV", type="csv")

if file:
    df_raw = pd.read_csv(file)
    df_raw.columns = df_raw.columns.str.strip()
    elements = [c for c in df_raw.columns if c not in ['Category', 'Label', 'Type']]

    # PHASE 1: Must be explicitly clicked
    if st.button("📊 Step 1: Run Primary Filter"):
        p1_data = []
        for i in range(0, len(df_raw) - (len(df_raw) % 4), 4):
            block = df_raw.iloc[i : i+4]
            name, tp = str(block['Label'].iloc[0]).strip(), str(block['Type'].iloc[0]).upper()
            target, dil = get_meta(name, tp)
            row = {'Index': (i//4)+1, 'Label': name, 'Type': tp, '_t': target, 'Dilution': dil}
            for el in elements:
                try:
                    c = block['Category'].str.lower()
                    avg = block[c.contains('average')][el].values[0]
                    rsd = float(block[c.contains('rsd')][el].values[0])
                    mql = float(block[c.contains('mql')][el].values[0])
                    sd  = float(block[c.contains('sd')][el].values[0])
                    if "<LQ" in str(avg) or float(avg) < mql:
                        row[el] = f"<{round(abs(sd * 10), 4)}" # QA: Anti-minus
                    else:
                        f = "!!" if rsd > rsd_h else ("!" if rsd > rsd_l else "")
                        row[el] = f"{float(avg)}{f}"
                except: row[el] = "n/a"
            p1_data.append(row)
        st.session_state.p1 = pd.DataFrame(p1_data)

    if 'p1' in st.session_state:
        st.subheader("Table 1: Primary Filtered")
        st.dataframe(st.session_state.p1.drop(columns=['_t']))

        # PHASE 2: Must be explicitly clicked
        if st.button("🚀 Step 2: Run Metrological Correction"):
            p1 = st.session_state.p1.copy()
            p2_res, p3_res = [], []
            blks = p1[p1['Type'] == 'BLK']
            avg_b = {el: np.mean([clean_num(v) for v in blks[el]]) if not blks.empty else 0.0 for el in elements}

            for _, row in p1.iterrows():
                r2, r3 = {'Index': row['Index'], 'Label': row['Label'], 'Type': row['Type']}, {'Index': row['Index'], 'Label': row['Label']}
                for el in elements:
                    raw_v = clean_num(row[el])
                    f = 1.0
                    # CCV Drift Logic
                    ccvs = p1[(p1['Type'] == 'CCV') & (p1['_t'].notnull())]
                    if not ccvs.empty:
                        pts = [c for _, c in ccvs.iterrows() if clean_num(c[el]) >= (c['_t'] * inc_t / 100)]
                        if pts:
                            best = min(pts, key=lambda x: abs(x['Index'] - row['Index']))
                            f = best['_t'] / clean_num(best[el])
                    
                    v_net = (raw_v * f) - (avg_b[el] if row['Type'] == 'S' else 0.0)
                    is_loq = "<" in str(row[el])
                    
                    # QA FINAL FIX: No minus allowed
                    final_v = max(0.0, raw_v * row['Dilution']) if is_loq else max(0.0, v_net * row['Dilution'])
                    
                    r2[el] = f"{'<' if is_loq else ''}{final_v:.4f}"
                    r3[el] = f"f:{f:.2f} B:{avg_b[el]:.2e}"
                p2_res.append(r2); p3_res.append(r3)
            
            st.session_state.p2 = pd.DataFrame(p2_res)
            st.session_state.p3 = pd.DataFrame(p3_res)

        # Persistence Check: Only show if they exist in state
        if 'p2' in st.session_state:
            st.subheader("Table 2: Final Results")
            st.dataframe(st.session_state.p2)
            
            if 'p3' in st.session_state:
                st.subheader("Table 3: Audit Trail")
                st.dataframe(st.session_state.p3)
            
            csv = st.session_state.p2.to_csv(index=False).encode('utf-8')
            st.download_button("📥 Download Report", csv, "Final_Report.csv", "text/csv")
