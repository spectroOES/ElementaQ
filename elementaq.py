import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO

# --- 1. SETTINGS & UI ---
st.set_page_config(layout="wide", page_title="ElementaQ")
st.title("🔬 ElementaQ: ICP-OES Analytical Engine v13.1")

# Функция для полной очистки при смене файла
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
    drift_window = st.number_input("CCV Match Window (+/- %)", 5.0, 50.0, 20.0)

# --- 2. HELPER FUNCTIONS (Логика без изменений) ---

def is_below_loq(avg_val, mql_val):
    if pd.isna(avg_val): return True
    s = str(avg_val).strip()
    if "<LQ" in s: return True
    try: return float(s) < mql_val
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

file_container = st.empty()
uploaded_file = file_container.file_uploader("Upload ICP CSV", type="csv", on_change=reset_all)

# Если файл удален - принудительно зануляем результаты
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

    # DRIFT INTERPOLATION
    for el in elements:
        ccv_pts = []
        for b in blocks:
            if 'CCV' in str(b['Type']):
                t = get_target(b['Type']); m = to_num(b['avg'][el])
                if t and m and m > 0: ccv_pts.append({'idx': b['idx'], 'f': t/m, 'target': t})
        
        for b in blocks:
            raw = to_num(b['avg'][el]); mql_v = to_num(b['mql'][el]) or 0.0
            if not raw or is_below_loq(b['avg'][el], mql_v):
                b['f_drift'][el] = 1.0; b['drift_note'][el] = "Below LOQ"
                continue
            v_pts = [p for p in ccv_pts if (1 - drift_window/100) * raw <= p['target'] <= (1 + drift_window/100) * raw]
            if not v_pts: b['f_drift'][el], b['drift_note'][el] = 1.0, "No CCV match"
            elif len(v_pts) == 1: b['f_drift'][el], b['drift_note'][el] = v_pts[0]['f'], f"Fixed({v_pts[0]['target']})"
            else:
                bef = [p for p in v_pts if p['idx'] <= b['idx']]; aft = [p for p in v_pts if p['idx'] > b['idx']]
                if not bef: best = min(aft, key=lambda x: x['idx']); b['f_drift'][el], b['drift_note'][el] = best['f'], f"First({best['target']})"
                elif not aft: best = max(bef, key=lambda x: x['idx']); b['f_drift'][el], b['drift_note'][el] = best['f'], f"Last({best['target']})"
                else:
                    p1, p2 = max(bef, key=lambda x: x['idx']), min(aft, key=lambda x: x['idx'])
                    w = (b['idx'] - p1['idx']) / (p2['idx'] - p1['idx'])
                    b['f_drift'][el] = p1['f'] + w * (p2['f'] - p1['f'])
                    b['drift_note'][el] = f"Interp({p1['target']}-{p2['target']})"

    # BLANKS
    avg_blanks = {}
    for el in elements:
        vals = [to_num(b['avg'][el]) * b['f_drift'][el] for b in blocks if any(x in str(b['Type']).upper() for x in ['BLK', 'MBB']) if to_num(b['avg'][el]) is not None]
        avg_blanks[el] = np.mean(vals) if vals else 0.0

    # TABLES
    t1_r, t2_r, t3_r = [], [], []
    for b in blocks:
        r1 = {'Label': b['Label'], 'Type': b['Type']}
        for el in elements:
            if is_below_loq(b['avg'][el], to_num(b['mql'][el]) or 0.0): r1[el] = f"<{round((to_num(b['sd'][el]) or 0.0)*10, 3)}"
            else:
                v = to_num(b['avg'][el]); r = to_num(b['rsd'][el]) or 0.0
                r1[el] = f"{v}{'!!' if r > rsd_h else ('!' if r > rsd_l else '')}"
        t1_r.append(r1)
        if str(b['Type']).startswith('S'):
            r2, r3 = {'Label': b['Label']}, {'Label': b['Label']}
            dil = get_target(b['Type']) or 1.0
            for el in elements:
                if is_below_loq(b['avg'][el], to_num(b['mql'][el]) or 0.0): r2[el], r3[el] = "N.D.", "Below LOQ"
                else:
                    v, f, bl = to_num(b['avg'][el]), b['f_drift'][el], avg_blanks[el]
                    r2[el] = round((v * f - bl) * dil, 4)
                    r3[el] = f"({v:.3f} * {f:.3f}[{b['drift_note'][el]}] - {bl:.3f}[BLK]) * {dil}"
            t2_r.append(r2); t3_r.append(r3)

    st.session_state.results = (pd.DataFrame(t1_r), pd.DataFrame(t2_r), pd.DataFrame(t3_r))

# --- 4. OUTPUT & EXPORT ---
if uploaded_file and st.session_state.results:
    t1, t2, t3 = st.session_state.results
    
    # Сборка одного файла: таблицы одна под другой
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        # Пишем всё на один лист "ElementaQ_Report"
        t1.to_excel(writer, sheet_name='ElementaQ_Report', startrow=1, index=False)
        curr_row = len(t1) + 4
        
        t2.to_excel(writer, sheet_name='ElementaQ_Report', startrow=curr_row, index=False)
        curr_row += len(t2) + 3
        
        t3.to_excel(writer, sheet_name='ElementaQ_Report', startrow=curr_row, index=False)
        
        # Добавляем заголовки прямо в Excel
        ws = writer.sheets['ElementaQ_Report']
        ws.write(0, 0, "TABLE 1: THRESHOLDS & FLAGS")
        ws.write(len(t1) + 3, 0, "TABLE 2: FINAL RESULTS")
        ws.write(len(t1) + len(t2) + 6, 0, "TABLE 3: MATH LOG")

    st.download_button("📥 Download ElementaQ Full Report", buffer.getvalue(), "ElementaQ_Report.xlsx")

    st.subheader("📊 1. Thresholds")
    st.dataframe(t1, use_container_width=True)
    st.subheader("✅ 2. Final Results")
    st.dataframe(t2, use_container_width=True)
    st.subheader("📝 3. Math Log")
    st.dataframe(t3, use_container_width=True)
