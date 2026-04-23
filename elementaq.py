import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO

# --- 1. SETTINGS & UI ---
st.set_page_config(layout="wide", page_title="ElementaQ")
st.title("🔬 ElementaQ: ICP-OES Analytical Engine v13.6")

def reset_all():
    st.session_state.results = None

if 'results' not in st.session_state:
    st.session_state.results = None

with st.sidebar:
    st.header("⚙️ QC Flags (RSD)")
    rsd_l = st.sidebar.slider("Yellow Flag %", 1.0, 15.0, 6.0)
    rsd_h = st.sidebar.slider("Red Flag %", 1.0, 25.0, 10.0)
    
    st.markdown("---")
    st.header("📈 Drift & Calibration")
    fit_window = st.number_input("CCV Match Window (Fit) +/- %", 5.0, 100.0, 20.0)
    d_deadband = st.number_input("Drift Deadband %", 0.0, 10.0, 5.0)
    d_max = st.number_input("Max Drift Allowed %", 5.0, 50.0, 10.0)

# --- 2. HELPER FUNCTIONS ---

def get_drift_factor(measured, nominal, deadband, max_drift):
    if not measured or not nominal: return 1.0, "None"
    diff = abs((measured - nominal) / nominal) * 100
    if diff <= deadband: return 1.0, "Stable"
    elif diff > max_drift: return 1.0, "QC FAIL"
    else: return nominal / measured, "Corrected"

def is_below_loq(avg_val, mql_val):
    if pd.isna(avg_val): return True
    s = str(avg_val).strip()
    if "<" in s: return True 
    try: return float(s) < mql_val
    except: return True

def to_num(val):
    if pd.isna(val): return None
    try:
        s = re.sub(r'[!<>]', '', str(val)).strip()
        return float(s)
    except: return None

def get_target(type_str):
    match = re.search(r'_([\d.]+)$', str(type_str))
    return float(match.group(1)) if match else None

# --- 3. PROCESSING ENGINE ---

uploaded_file = st.file_uploader("Upload ICP CSV", type="csv", on_change=reset_all)

if not uploaded_file:
    st.session_state.results = None

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

    # Drift & Blanks Logic
    for el in elements:
        ccv_pts = []
        for b in blocks:
            if 'CCV' in str(b['Type']):
                nom = get_target(b['Type'])
                meas = to_num(b['avg'][el])
                if nom and meas:
                    f, note = get_drift_factor(meas, nom, d_deadband, d_max)
                    ccv_pts.append({'idx': b['idx'], 'f': f, 'target': nom, 'status': note})
        
        for b in blocks:
            raw = to_num(b['avg'][el]); mql_v = to_num(b['mql'][el]) or 0.0
            if not raw or is_below_loq(b['avg'][el], mql_v):
                b['f_drift'][el], b['drift_note'][el] = 1.0, "Below LOQ"
                continue
            
            v_pts = [p for p in ccv_pts if (1 - fit_window/100) * raw <= p['target'] <= (1 + fit_window/100) * raw]
            if not v_pts: b['f_drift'][el], b['drift_note'][el] = 1.0, "No Fit"
            else:
                p = v_pts[0] # Simplified selection
                b['f_drift'][el], b['drift_note'][el] = p['f'], f"{p['status']}({p['target']})"

    avg_blanks = {}
    for el in elements:
        vals = [to_num(b['avg'][el]) * b['f_drift'][el] for b in blocks if any(x in str(b['Type']).upper() for x in ['BLK', 'MBB'])]
        avg_blanks[el] = np.mean(vals) if vals else 0.0

    # Synchronization & Tables
    t1_r, t2_r, t3_r = [], [], []
    for b in blocks:
        r1 = {'Label': b['Label'], 'Type': b['Type']}
        raw_loq_values = {} 
        
        for el in elements:
            sd_val = to_num(b['sd'][el]) or 0.0
            loq_val = round(sd_val * 10, 4)
            
            if is_below_loq(b['avg'][el], to_num(b['mql'][el]) or 0.0): 
                r1[el] = f"<{loq_val}"
                raw_loq_values[el] = loq_val
            else:
                v, r = to_num(b['avg'][el]), to_num(b['rsd'][el]) or 0.0
                r1[el] = f"{v}{'!!' if r > rsd_h else ('!' if r > rsd_l else '')}"
                raw_loq_values[el] = None
        t1_r.append(r1)
        
        if str(b['Type']).startswith('S'):
            r2, r3 = {'Label': b['Label']}, {'Label': b['Label']}
            dil = get_target(b['Type']) or 1.0
            for el in elements:
                # Если в T1 стоит <, запрещаем все поправки, кроме разбавления
                if raw_loq_values[el] is not None:
                    final_val = round(raw_loq_values[el] * dil, 4)
                    r2[el] = f"<{final_val}"
                    r3[el] = f"LOQ {raw_loq_values[el]} * Dil {dil} (Math Locked)"
                else:
                    v, f, bl = to_num(b['avg'][el]), b['f_drift'][el], avg_blanks[el]
                    res = (v * f - bl) * dil
                    r2[el] = round(res, 4)
                    r3[el] = f"({v:.3f} * {f:.3f}[{b['drift_note'][el]}] - {bl:.3f}[BLK]) * {dil}"
            t2_r.append(r2); t3_r.append(r3)

    st.session_state.results = (pd.DataFrame(t1_r), pd.DataFrame(t2_r), pd.DataFrame(t3_r))

# --- 4. OUTPUT ---
if uploaded_file and st.session_state.results:
    t1, t2, t3 = st.session_state.results
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        t1.to_excel(writer, sheet_name='ElementaQ_Report', startrow=1, index=False)
        t2.to_excel(writer, sheet_name='ElementaQ_Report', startrow=len(t1)+5, index=False)
        t3.to_excel(writer, sheet_name='ElementaQ_Report', startrow=len(t1)+len(t2)+9, index=False)
    
    st.download_button("📥 Download Report", buffer.getvalue(), "ElementaQ_Report.xlsx")
    st.subheader("📊 1. Thresholds"); st.dataframe(t1, use_container_width=True)
    st.subheader("✅ 2. Final Results"); st.dataframe(t2, use_container_width=True)
    st.subheader("📝 3. Math Log"); st.dataframe(t3, use_container_width=True)
