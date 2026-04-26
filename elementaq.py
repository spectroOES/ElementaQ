import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO

# --- 1. НАСТРОЙКИ И ИНТЕРФЕЙС ---
st.set_page_config(layout="wide", page_title="ElementaQ")
st.title("🔬 ElementaQ: ICP-OES Analytical Engine v13.11")

def reset_all():
    st.session_state.results = None

if 'results' not in st.session_state:
    st.session_state.results = None

with st.sidebar:
    st.header("⚙️ QC Settings")
    rsd_l = st.sidebar.slider("Yellow Flag RSD %", 1.0, 15.0, 6.0)
    rsd_h = st.sidebar.slider("Red Flag RSD %", 1.0, 25.0, 10.0)
    
    st.markdown("---")
    st.header("📈 Drift Calibration (Gaid v2.0)")
    fit_window = st.number_input("CCV Match Window (+/- %)", 5.0, 100.0, 20.0)
    d_deadband = st.number_input("Tier A: No Correction Zone %", 0.0, 10.0, 5.0)
    d_max = st.number_input("Tier C: Failure Limit %", 5.0, 50.0, 10.0)

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def to_num(val):
    if pd.isna(val): return None
    try:
        # Очистка строки от флагов и знаков < >
        s = re.sub(r'[!<>]', '', str(val)).strip()
        return float(s)
    except: return None

def get_target(type_str):
    # Извлечение номинала из названия, напр. CCV_0.1 -> 0.1
    match = re.search(r'_([\d.]+)$', str(type_str))
    return float(match.group(1)) if match else None

def calculate_f_drift(measured, target, deadband, failure_limit):
    if not measured or not target or target == 0: return 1.0, "None"
    diff = abs((measured - target) / target) * 100
    if diff <= deadband: 
        return 1.0, "Tier A (Stable)"
    elif diff > failure_limit: 
        return 1.0, "Tier C (QC FAIL)"
    else: 
        return target / measured, "Tier B (Corrected)"

# --- 3. ДВИЖОК ОБРАБОТКИ ---

uploaded_file = st.file_uploader("Upload ICP CSV", type="csv", on_change=reset_all)

if uploaded_file and st.button("🚀 Execute Analysis"):
    try:
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

        # 1. ЛОГИКА ДРЕЙФА (Линейная интерполяция vs Single Point)
        for el in elements:
            ccv_data = []
            for b in blocks:
                if 'CCV' in str(b['Type']).upper():
                    target = get_target(b['Type'])
                    measured = to_num(b['avg'][el])
                    if target is not None and measured is not None:
                        f, note = calculate_f_drift(measured, target, d_deadband, d_max)
                        ccv_data.append({'idx': b['idx'], 'target': target, 'f': f, 'note': note})

            for b in blocks:
                raw = to_num(b['avg'][el])
                if raw is None:
                    b['f_drift'][el], b['drift_note'][el] = 1.0, "No Data"
                    continue

                # Фильтр 1: Concentration Match (+/- 20%) [cite: 32, 58, 94]
                valid_ccvs = [p for p in ccv_data if (1 - fit_window/100) * raw <= p['target'] <= (1 + fit_window/100) * raw]
                
                if not valid_ccvs:
                    b['f_drift'][el], b['drift_note'][el] = 1.0, "No Fit"
                    continue

                before = [p for p in valid_ccvs if p['idx'] < b['idx']]
                after = [p for p in valid_ccvs if p['idx'] > b['idx']]

                if before and after:
                    p_start, p_end = before[-1], after[0]
                    # Фильтр 3: Identical Target Check [cite: 39, 103]
                    if p_start['target'] == p_end['target']:
                        if "QC FAIL" in p_start['note'] or "QC FAIL" in p_end['note']:
                            b['f_drift'][el], b['drift_note'][el] = 1.0, "Tier C FAIL"
                        else:
                            # Линейная интерполяция 
                            dist = p_end['idx'] - p_start['idx']
                            pos = b['idx'] - p_start['idx']
                            f_interp = p_start['f'] + (p_end['f'] - p_start['f']) * (pos / dist)
                            b['f_drift'][el], b['drift_note'][el] = f_interp, f"Interp({p_start['target']})"
                    else:
                        p_nearest = min([p_start, p_end], key=lambda x: abs(x['idx'] - b['idx']))
                        b['f_drift'][el], b['drift_note'][el] = p_nearest['f'], f"SinglePt({p_nearest['target']})"
                elif before or after:
                    p_nearest = before[-1] if before else after[0]
                    b['f_drift'][el], b['drift_note'][el] = p_nearest['f'], f"SinglePt({p_nearest['target']})"
                else:
                    b['f_drift'][el], b['drift_note'][el] = 1.0, "No Match"

        # 2. БЛАНКИ (Analytical Blank Logic) [cite: 18, 62, 84]
        avg_blanks = {}
        for el in elements:
            vals = [to_num(b['avg'][el]) * b['f_drift'].get(el, 1.0) for b in blocks if any(x in str(b['Type']).upper() for x in ['BLK', 'MBB'])]
            avg_blanks[el] = np.mean(vals) if vals else 0.0

        # 3. ГЕНЕРАЦИЯ ТАБЛИЦ
        t1_r, t2_r, t3_r = [], [], []
        mql_storage = {el: [] for el in elements}

        for b in blocks:
            r1, is_loq_row = {'Label': b['Label'], 'Type': b['Type']}, {}
            for el in elements:
                raw_v, mql_v = to_num(b['avg'][el]), to_num(b['mql'][el]) or 0.0
                sd_v = to_num(b['sd'][el]) or 0.0
                loq_val = round(sd_v * 10, 4)
                mql_storage[el].append(loq_val)

                # Проверка порога (Таблица 1)
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
                        # HARD LOCK: Только разбавление для результатов ниже LOQ 
                        r2[el] = f"<{round(is_loq_row[el] * dil, 4)}"
                        r3[el] = f"LOQ {is_loq_row[el]} * Dil {dil} (Locked)"
                    else:
                        v, f, bl = to_num(b['avg'][el]), b['f_drift'][el], avg_blanks[el]
                        res = (v * f - bl) * dil
                        r2[el] = round(res, 4)
                        r3[el] = f"({v:.3f} * {f:.3f}[{b['drift_note'][el]}] - {bl:.3f}[BLK]) * {dil}"
                t2_r.append(r2); t3_r.append(r3)

        # Добавляем MQL Reference Footer 
        mql_row = {'Label': '--- MQL REFERENCE ---', 'Type': 'REF'}
        for el in elements: 
            mql_row[el] = round(np.mean(mql_storage[el]), 4) if mql_storage[el] else 0.0
        t1_r.append(mql_row)

        st.session_state.results = (pd.DataFrame(t1_r), pd.DataFrame(t2_r), pd.DataFrame(t3_r))
    except Exception as e:
        st.error(f"Critical error during execution: {e}")

# --- 4. ВЫВОД РЕЗУЛЬТАТОВ ---
if st.session_state.results:
    t1, t2, t3 = st.session_state.results
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        t1.to_excel(writer, sheet_name='ElementaQ_Report', startrow=1, index=False)
        t2.to_excel(writer, sheet_name='ElementaQ_Report', startrow=len(t1)+5, index=False)
    
    st.download_button("📥 Download Excel Report", buffer.getvalue(), "ElementaQ_v13_11.xlsx")
    st.subheader("📊 1. Thresholds (with MQL Reference)"); st.dataframe(t1, use_container_width=True)
    st.subheader("✅ 2. Final Results"); st.dataframe(t2, use_container_width=True)
    st.subheader("📝 3. Math Log & Audit Trail"); st.dataframe(t3, use_container_width=True)
