import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO

# --- 1. SETTINGS & UI ---
st.set_page_config(layout="wide", page_title="ElementaQ")
st.title("🔬 ElementaQ: ICP-OES Analytical Engine v13.8")

def reset_all():
    st.session_state.results = None

if 'results' not in st.session_state:
    st.session_state.results = None

with st.sidebar:
    st.header("⚙️ QC Settings")
    rsd_l = st.sidebar.slider("Yellow Flag RSD %", 1.0, 15.0, 6.0)
    rsd_h = st.sidebar.slider("Red Flag RSD %", 1.0, 25.0, 10.0)
    
    st.markdown("---")
    st.header("📈 Drift Calibration")
    fit_window = st.number_input("CCV Match Window (+/- %)", 5.0, 100.0, 20.0)
    d_deadband = st.number_input("Drift Deadband %", 0.0, 10.0, 5.0)
    d_max = st.number_input("Max Drift Allowed %", 5.0, 50.0, 10.0)

# --- 2. HELPER FUNCTIONS ---

def to_num(val):
    if pd.isna(val): return None
    try:
        s = re.sub(r'[!<>]', '', str(val)).strip()
        return float(s)
    except: return None

def get_drift_factor(measured, nominal, deadband, max_drift):
    if not measured or not nominal: return 1.0, "None"
    diff = abs((measured - nominal) / nominal) * 100
    if diff <= deadband: return 1.0, "Stable"
    elif diff > max_drift: return 1.0, "QC FAIL"
    else: return nominal / measured, "Corrected"

def get_target(type_str):
    match = re.search(r'_([\d.]+)$', str(type_str))
    return float(match.group(1)) if match else None

# --- 3. PROCESSING ENGINE ---

uploaded_file = st.file_uploader("Upload ICP CSV", type="csv", on_change=reset_all)

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
            blocks.append({'idx': i, 'Label': avg['Label'], 'Type': avg['Type'], 'avg': avg, 'sd': sd, 'rsd': rsd, 'mql': mql, 'f_drift': {}, 'drift_note': {}})
        except: continue

    # 1. Drift
    for el in elements:
        ccv_pts = []
        for b in blocks:
            if 'CCV' in str(b['Type']):
                nom, meas = get_target(b['Type']), to_num(b['avg'][el])
                if nom and meas:
                    f, note = get_drift_factor(meas, nom, d_deadband, d_max)
                    ccv_pts.append({'idx': b['idx'], 'f': f, 'target': nom, 'status': note})
        for b in blocks:
            raw = to_num(b['avg'][el])
            v_pts = [p for p in ccv_pts if (1 - fit_window/100) * (raw or 0) <= p['target'] <= (1 + fit_window/100) * (raw or 0)]
            if not v_pts or raw is None: b['f_drift'][el], b['drift_note'][el] = 1.0, "No Fit"
            else:
                p = min(v_pts, key=lambda x: abs(x['idx'] - b['idx']))
                b['f_drift'][el], b['drift_note'][el] = p['f'], f"{p['status']}({p['target']})"

    # 2. Blanks
    avg_blanks = {}
    for el in elements:
        vals = [to_num(b['avg'][el]) * b['f_drift'].get(el, 1.0) for b in blocks if any(x in str(b['Type']).upper() for x in ['BLK', 'MBB']) if to_num(b['avg'][el]) is not None]
        avg_blanks[el] = np.mean(vals) if vals else 0.0

    # 3. Tables
    t1_r, t2_r, t3_r = [], [], []
    for b in blocks:
        r1, is_loq_row = {'Label': b['Label'], 'Type': b['Type']}, {}
        for el in elements:
            raw_v, mql_v = to_num(b['avg'][el]), to_num(b['mql'][el]) or 0.0
            sd_v = to_num(b['sd'][el]) or 0.0
            loq_val = round(sd_v * 10, 4)
            if raw_v is None or raw_v < mql_v or "<" in str(b['avg'][el]):
                r1[el] = f"<{loq_val}"; is_loq_row[el] = loq_val
            else:
                rsd_v = to_num(b['rsd'][el]) or 0.0
                flag = "!!" if rsd_v > rsd_h else ("!" if rsd_v > rsd_l else "")
                r1[el] = f"{raw_v}{flag}"; is_loq_row[el] = None
        t1_r.append(r1)
        
        if str(b['Type']).startswith('S'):
            r2, r3 = {'Label': b['Label']}, {'Label': b['Label']}
            dil = get_target(b['Type']) or 1.0
            for el in elements:
                if is_loq_row[el] is not None:
                    r2[el] = f"<{round(is_loq_row[el] * dil, 4)}"
                    r3[el] = f"LOQ {is_loq_row[el]} * Dil {dil} (Locked)"
                else:
                    v, f, bl = to_num(b['avg'][el]), b['f_drift'][el], avg_blanks[el]
                    r2[el] = round((v * f - bl) * dil, 4)
                    r3[el] = f"({v:.3f} * {f:.3f}[{b['drift_note'][el]}] - {bl:.3f}[BLK]) * {dil}"
            t2_r.append(r2); t3_r.append(r3)

    # 4. Save to session state
    st.session_state.results = (pd.DataFrame(t1_r), pd.DataFrame(t2_r), pd.DataFrame(t3_r))

# --- 4. OUTPUT & EXCEL ---
if st.session_state.results:
    t1, t2, t3 = st.session_state.results
    
    # Регенерация Excel файла для кнопки скачивания
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        t1.to_excel(writer, sheet_name='ElementaQ_Report', startrow=1, index=False)
        t2.to_excel(writer, sheet_name='ElementaQ_Report', startrow=len(t1)+5, index=False)
        t3.to_excel(writer, sheet_name='ElementaQ_Report', startrow=len(t1)+len(t2)+9, index=False)
    
    st.download_button("📥 Download Report", buffer.getvalue(), "ElementaQ_Report.xlsx")
    st.subheader("📊 1. Thresholds"); st.dataframe(t1, use_container_width=True)
    st.subheader("✅ 2. Final Results"); st.dataframe(t2, use_container_width=True)
    st.subheader("📝 3. Math Log"); st.dataframe(t3, use_container_width=True)
