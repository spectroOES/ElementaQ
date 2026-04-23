import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO

# --- 1. SETTINGS & UI ---
st.set_page_config(layout="wide", page_title="ElementaQ")
st.title("🔬 ElementaQ: ICP-OES Analytical Engine v13.0")

if 'results' not in st.session_state:
    st.session_state.results = None

with st.sidebar:
    st.header("⚙️ QC Settings")
    rsd_l = st.sidebar.slider("Yellow Flag RSD %", 1.0, 15.0, 6.0)
    rsd_h = st.sidebar.slider("Red Flag RSD %", 1.0, 25.0, 10.0)
    st.markdown("---")
    st.header("📈 Drift Calibration")
    drift_window = st.number_input("CCV Match Window (+/- %)", 5.0, 50.0, 20.0)

# --- 2. HELPER FUNCTIONS ---

def is_below_loq(avg_val, mql_val):
    if pd.isna(avg_val): return True
    s = str(avg_val).strip()
    if "<LQ" in s: return True
    try:
        return float(s) < mql_val
    except: return True

def to_num(val):
    if pd.isna(val): return None
    try:
        s = str(val).replace('!', '').strip()
        return float(s)
    except: return None

def get_target(type_str):
    match = re.search(r'_([\d.]+)$', str(type_str))
    return float(match.group(1)) if match else None

# --- 3. PROCESSING ENGINE ---

uploaded_file = st.file_uploader("Upload ICP CSV", type="csv")

if uploaded_file and st.button("🚀 Execute Analysis"):
    df = pd.read_csv(uploaded_file)
    df.columns = df.columns.str.strip()
    elements = [c for c in df.columns if c not in ['Category', 'Label', 'Type']]
    
    blocks = []
    for i in range(0, len(df) - (len(df) % 4), 4):
        sub = df.iloc[i:i+4]
        try:
            avg = sub[sub['Category'].str.contains('average', case=False)].iloc[0]
            sd  = sub[sub['Category'].str.contains('SD', case=False)].iloc[0]
            rsd = sub[sub['Category'].str.contains('RSD', case=False)].iloc[0]
            mql = sub[sub['Category'].str.contains('MQL', case=False)].iloc[0]
            blocks.append({
                'idx': i, 'Label': avg['Label'], 'Type': avg['Type'],
                'avg': avg, 'sd': sd, 'rsd': rsd, 'mql': mql,
                'f_drift': {}, 'drift_note': {}
            })
        except: continue

    # PHASE 1: LINEAR DRIFT INTERPOLATION (По позиции в автосамплере)
    for el in elements:
        ccv_points = []
        for b in blocks:
            if 'CCV' in str(b['Type']):
                target = get_target(b['Type'])
                measured = to_num(b['avg'][el])
                if target and measured and measured > 0:
                    ccv_points.append({
                        'idx': b['idx'],
                        'f': target / measured,
                        'target': target
                    })
        
        for b in blocks:
            raw_val = to_num(b['avg'][el])
            mql_val = to_num(b['mql'][el]) or 0.0
            
            if not raw_val or is_below_loq(b['avg'][el], mql_val):
                b['f_drift'][el] = 1.0
                b['drift_note'][el] = "Below LOQ"
                continue

            valid_pts = [p for p in ccv_points if (1 - drift_window/100) * raw_val <= p['target'] <= (1 + drift_window/100) * raw_val]
            
            if not valid_pts:
                b['f_drift'][el] = 1.0
                b['drift_note'][el] = "No CCV match"
            elif len(valid_pts) == 1:
                b['f_drift'][el] = valid_pts[0]['f']
                b['drift_note'][el] = f"Fixed({valid_pts[0]['target']})"
            else:
                before = [p for p in valid_pts if p['idx'] <= b['idx']]
                after = [p for p in valid_pts if p['idx'] > b['idx']]
                
                if not before: 
                    best = min(after, key=lambda x: x['idx'])
                    b['f_drift'][el], b['drift_note'][el] = best['f'], f"First({best['target']})"
                elif not after: 
                    best = max(before, key=lambda x: x['idx'])
                    b['f_drift'][el], b['drift_note'][el] = best['f'], f"Last({best['target']})"
                else: 
                    p1 = max(before, key=lambda x: x['idx'])
                    p2 = min(after, key=lambda x: x['idx'])
                    # Линейная интерполяция дрейфа
                    weight = (b['idx'] - p1['idx']) / (p2['idx'] - p1['idx'])
                    b['f_drift'][el] = p1['f'] + weight * (p2['f'] - p1['f'])
                    b['drift_note'][el] = f"Interp({p1['target']}-{p2['target']})"

    # PHASE 2: MEAN BLANK
    avg_blanks = {}
    for el in elements:
        vals = []
        for b in blocks:
            if any(x in str(b['Type']).upper() for x in ['BLK', 'MBB']):
                v = to_num(b['avg'][el])
                if v is not None: vals.append(v * b['f_drift'][el])
        avg_blanks[el] = np.mean(vals) if vals else 0.0

    # PHASE 3: TABLES
    t1_r, t2_r, t3_r = [], [], []
    for b in blocks:
        row1 = {'Label': b['Label'], 'Type': b['Type']}
        for el in elements:
            mql_v = to_num(b['mql'][el]) or 0.0
            if is_below_loq(b['avg'][el], mql_v):
                sd_v = to_num(b['sd'][el]) or 0.0
                row1[el] = f"<{round(sd_v * 10, 3)}"
            else:
                val = to_num(b['avg'][el])
                rsd_v = to_num(b['rsd'][el]) or 0.0
                flag = "!!" if rsd_v > rsd_h else ("!" if rsd_v > rsd_l else "")
                row1[el] = f"{val}{flag}"
        t1_r.append(row1)

        if str(b['Type']).startswith('S'):
            row2, row3 = {'Label': b['Label']}, {'Label': b['Label']}
            dil = get_target(b['Type']) or 1.0
            for el in elements:
                mql_v = to_num(b['mql'][el]) or 0.0
                if is_below_loq(b['avg'][el], mql_v):
                    row2[el], row3[el] = "N.D.", "Below LOQ"
                else:
                    v = to_num(b['avg'][el]); f = b['f_drift'][el]
                    bl = avg_blanks[el]; res = (v * f - bl) * dil
                    row2[el] = round(res, 4)
                    row3[el] = f"({v:.3f} * {f:.3f}[{b['drift_note'][el]}] - {bl:.3f}[BLK]) * {dil}"
            t2_r.append(row2); t3_r.append(row3)

    st.session_state.results = (pd.DataFrame(t1_r), pd.DataFrame(t2_r), pd.DataFrame(t3_r))

# --- 4. OUTPUT & EXPORT ---
if st.session_state.results:
    t1, t2, t3 = st.session_state.results
    
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        t1.to_excel(writer, sheet_name='1_Thresholds', index=False)
        t2.to_excel(writer, sheet_name='2_Final_Results', index=False)
        t3.to_excel(writer, sheet_name='3_Math_Log', index=False)
    
    st.download_button(
        label="📥 Download ElementaQ Full Report",
        data=buffer.getvalue(),
        file_name="ElementaQ_Analysis.xlsx",
        mime="application/vnd.ms-excel"
    )

    st.subheader("📊 1. Thresholds & Flags")
    st.dataframe(t1, use_container_width=True)
    
    st.subheader("✅ 2. Final Results")
    st.dataframe(t2, use_container_width=True)
    
    st.subheader("📝 3. Detailed Math Log")
    st.dataframe(t3, use_container_width=True)
