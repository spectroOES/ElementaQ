import streamlit as st
import pandas as pd
import numpy as np
import re
import io
from io import BytesIO

# --- 1. SETTINGS & UI ---
st.set_page_config(layout="wide", page_title="Rosen ICP Processor")
st.title("🔬 ICP-OES Analytical Engine v12.0")

# Инициализация сессии, чтобы данные не пропадали при нажатии кнопок
if 'results' not in st.session_state:
    st.session_state.results = None

with st.sidebar:
    st.header("⚙️ QC Settings")
    rsd_l = st.sidebar.slider("Yellow Flag RSD %", 1.0, 15.0, 6.0)
    rsd_h = st.sidebar.slider("Red Flag RSD %", 1.0, 25.0, 10.0)
    st.markdown("---")
    st.header("📈 Drift Calibration")
    drift_window = st.number_input("CCV Match Window (+/- %)", 5.0, 50.0, 20.0)

# --- 2. HELPER FUNCTIONS (Логика без изменений) ---

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

# Расчет запускается ТОЛЬКО по кнопке
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
                'f_drift': {}, 'ccv_name': {}
            })
        except: continue

    all_ccvs = [b for b in blocks if 'CCV' in str(b['Type'])]

    # PHASE 1: DRIFT
    for b in blocks:
        for el in elements:
            f, ccv_label = 1.0, "None"
            raw_val = to_num(b['avg'][el])
            mql_val = to_num(b['mql'][el]) or 0.0
            if raw_val and not is_below_loq(b['avg'][el], mql_val):
                matches = []
                for ccv in all_ccvs:
                    target = get_target(ccv['Type'])
                    measured = to_num(ccv['avg'][el])
                    if target and measured and measured > 0:
                        if (1 - drift_window/100) * raw_val <= target <= (1 + drift_window/100) * raw_val:
                            matches.append({'f': target / measured, 'dist': abs(ccv['idx'] - b['idx']), 'name': f"CCV_{target}"})
                if matches:
                    best = min(matches, key=lambda x: x['dist'])
                    f, ccv_label = best['f'], best['name']
            b['f_drift'][el] = f
            b['ccv_name'][el] = ccv_label

    # PHASE 2: MEAN BLANK
    avg_blanks = {}
    for el in elements:
        vals = []
        for b in blocks:
            if any(x in str(b['Type']).upper() for x in ['BLK', 'MBB']):
                v = to_num(b['avg'][el])
                if v is not None: vals.append(v * b['f_drift'][el])
        avg_blanks[el] = np.mean(vals) if vals else 0.0

    # PHASE 3: TABLES GENERATION
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
                    v = to_num(b['avg'][el])
                    f = b['f_drift'][el]
                    bl = avg_blanks[el]
                    res = (v * f - bl) * dil
                    row2[el] = round(res, 4)
                    row3[el] = f"({v:.3f} * {f:.3f}[{b['ccv_name'][el]}] - {bl:.3f}[BLK]) * {dil}"
            t2_r.append(row2)
            t3_r.append(row3)

    # Сохраняем в сессию
    st.session_state.results = (pd.DataFrame(t1_r), pd.DataFrame(t2_r), pd.DataFrame(t3_r))

# --- 4. OUTPUT & EXPORT ---

if st.session_state.results:
    t1, t2, t3 = st.session_state.results
    
    # Кнопка скачивания Excel со всеми 3 листами
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        t1.to_excel(writer, sheet_name='1_Thresholds', index=False)
        t2.to_excel(writer, sheet_name='2_Final_Results', index=False)
        t3.to_excel(writer, sheet_name='3_Math_Log', index=False)
    
    st.download_button(
        label="📥 Download Full Excel Report",
        data=buffer.getvalue(),
        file_name="ICP_Analysis_Report.xlsx",
        mime="application/vnd.ms-excel"
    )

    tab1, tab2, tab3 = st.tabs(["📊 1. Thresholds", "✅ 2. Final Results", "📝 3. Math Log"])
    with tab1: st.dataframe(t1)
    with tab2: st.dataframe(t2)
    with tab3: st.dataframe(t3)
