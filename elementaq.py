import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO

# ==================== 1. НАСТРОЙКИ И ИНТЕРФЕЙС ====================
st.set_page_config(layout="wide", page_title="ElementaQ v14.0")
st.title("⚗️ ElementaQ: ICP-OES Analytical Engine v14.0")
st.caption("Metrology-compliant drift correction with 3-tier filtering & Smart Blank Logic")

def reset_all():
    st.session_state.results = None

if 'results' not in st.session_state:
    st.session_state.results = None

with st.sidebar:
    st.header("🔧 QC Settings")
    rsd_l = st.slider("🟡 Yellow Flag RSD %", 1.0, 15.0, 6.0)
    rsd_h = st.slider("🔴 Red Flag RSD %", 1.0, 25.0, 10.0)
    
    st.markdown("---")
    st.header("📊 Drift Calibration (Chapter 3-5)")
    fit_window = st.number_input(
        "Filter #1: CCV Match Window (±%)", 
        5.0, 100.0, 20.0, 
        help="Only CCVs with TARGET within sample ± this % are considered"
    )
    d_deadband = st.number_input(
        "Tier A: Deadband % (No Correction)", 
        0.0, 10.0, 5.0
    )
    d_max = st.number_input(
        "Tier C: Max Drift % (QC FAIL)", 
        5.0, 50.0, 10.0,
        help="Drift > this value blocks correction"
    )
    
    st.info("📌 Filter #3: Interpolation requires IDENTICAL CCV targets")

# ==================== 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def to_num(val):
    """Конвертирует значение CSV в float, обрабатывая <LQ, N/A и т.д."""
    if pd.isna(val):
        return None
    try:
        s = re.sub(r'[<>!]', '', str(val)).strip()
        return float(s)
    except:
        return None

def get_target(type_str):
    """Извлекает TARGET концентрацию из имени пробы: 'CCV_0.1' → 0.1"""
    match = re.search(r'_([\d.]+)$', str(type_str))
    return float(match.group(1)) if match else None

def calculate_drift_tier(found, target, deadband, max_drift):
    """
    Глава 3: Трёхуровневая оценка дрейфа
    Returns: (factor, status_string, is_fail)
    """
    if found is None or target is None or target == 0:
        return 1.0, "Invalid", False
    
    deviation_pct = abs((found - target) / target) * 100
    
    if deviation_pct <= deadband:
        # Tier A: Статистически незначимый дрейф
        return 1.0, f"Stable({deviation_pct:.1f}%)", False
    elif deviation_pct > max_drift:
        # Tier C: Катастрофический дрейф — блокировка
        return 1.0, f"QC FAIL({deviation_pct:.1f}%)", True
    else:
        # Tier B: Корректируемый дрейф
        factor = target / found
        return factor, f"Corrected({deviation_pct:.1f}%)", False

def check_concentration_match(sample_conc, ccv_target, window_pct):
    """
    Фильтр №1: Соответствие концентрации (Chapter 4)
    Проверяет: ccv_target ∈ [sample_conc × (1±window/100)]
    """
    if sample_conc is None or ccv_target is None:
        return False
    # Защита от деления на ноль для нулевых концентраций
    if sample_conc == 0:
        return ccv_target == 0
    lower = sample_conc * (1 - window_pct / 100)
    upper = sample_conc * (1 + window_pct / 100)
    return lower <= ccv_target <= upper

def interpolate_factor(idx_sample, idx_start, idx_end, f_start, f_end):
    """
    Глава 5: Формула линейной интерполяции
    fi = fstart + (fend − fstart) × (i − istart) / (iend − istart)
    """
    if idx_end == idx_start:
        return f_start
    fraction = (idx_sample - idx_start) / (idx_end - idx_start)
    return f_start + (f_end - f_start) * fraction

# ==================== 3. ОБРАБОТКА ДАННЫХ ====================

uploaded_file = st.file_uploader("📁 Upload ICP-OES CSV", type="csv", on_change=reset_all)

if uploaded_file and st.button("🚀 Execute Analysis", type="primary"):
    df = pd.read_csv(uploaded_file)
    df.columns = df.columns.str.strip()
    
    # Определяем колонки с элементами (исключаем метаданные)
    metadata_cols = ['Category', 'Label', 'Type']
    elements = [c for c in df.columns if c not in metadata_cols]
    
    # Парсим данные в блоки по 4 строки: Average | SD | RSD | MQL
    blocks = []
    for i in range(0, len(df) - (len(df) % 4), 4):
        sub = df.iloc[i:i+4]
        try:
            avg_row = sub[sub['Category'].str.contains('average', case=False, na=False)].iloc[0]
            sd_row = sub[sub['Category'].str.contains('SD', case=False, na=False)].iloc[0]
            rsd_row = sub[sub['Category'].str.contains('RSD', case=False, na=False)].iloc[0]
            mql_row = sub[sub['Category'].str.contains('MQL', case=False, na=False)].iloc[0]
            
            blocks.append({
                'idx': i // 4,
                'Label': avg_row['Label'],
                'Type': avg_row['Type'],
                'avg': avg_row, 'sd': sd_row, 'rsd': rsd_row, 'mql': mql_row,
                'f_drift': {}, 'drift_note': {}, 'qc_fail': {}
            })
        except IndexError:
            continue
    
    # === ШАГ 1: Пре-расчёт всех CCV для каждого элемента ===
    ccv_registry = {}
    for el in elements:
        ccv_registry[el] = []
        for b in blocks:
            if 'CCV' in str(b['Type']).upper():
                target = get_target(b['Type'])      # TARGET из имени (константа)
                found = to_num(b['avg'][el])         # FOUND с детектора (измерение)
                
                if target and found is not None:
                    factor, status, is_fail = calculate_drift_tier(
                        found, target, d_deadband, d_max
                    )
                    ccv_registry[el].append({
                        'idx': b['idx'],
                        'target': target,    # Для Фильтров #1 и #3
                        'found': found,      # Для расчёта фактора
                        'factor': factor,
                        'status': status,
                        'qc_fail': is_fail   # Для Фильтра #2
                    })
    
    # === ШАГ 2: Применение дрейф-коррекции к каждой пробе ===
    for b in blocks:
        for el in elements:
            raw_conc = to_num(b['avg'][el])
            
            # 🎯 ФИЛЬТР №1: Concentration Match
            candidates = [
                ccv for ccv in ccv_registry[el]
                if check_concentration_match(raw_conc, ccv['target'], fit_window)
            ]
            
            if not candidates or raw_conc is None:
                b['f_drift'][el] = 1.0
                b['drift_note'][el] = "No Fit"
                b['qc_fail'][el] = False
                continue
            
            # Находим ближайший CCV ДО и ПОСЛЕ пробы (по индексу)
            before = [c for c in candidates if c['idx'] <= b['idx']]
            after = [c for c in candidates if c['idx'] >= b['idx']]
            nearest_before = max(before, key=lambda x: x['idx']) if before else None
            nearest_after = min(after, key=lambda x: x['idx']) if after else None
            
            # 🎯 ФИЛЬТР №2: Tier C Failure Check
            if (nearest_before and nearest_before['qc_fail']) or \
               (nearest_after and nearest_after['qc_fail']):
                b['f_drift'][el] = 1.0
                b['drift_note'][el] = "QC FAIL"
                b['qc_fail'][el] = True
                continue
            
            # 🎯 ФИЛЬТР №3: Identical Aliquot Rule
            if nearest_before and nearest_after:
                # Оба стандарта найдены — проверяем идентичность TARGET
                if abs(nearest_before['target'] - nearest_after['target']) < 1e-9:
                    # ✅ Одинаковые таргеты → линейная интерполяция (Глава 5)
                    f_interp = interpolate_factor(
                        b['idx'],
                        nearest_before['idx'], nearest_after['idx'],
                        nearest_before['factor'], nearest_after['factor']
                    )
                    b['f_drift'][el] = f_interp
                    b['drift_note'][el] = f"Interp({nearest_before['target']})"
                else:
                    # ❌ Разные таргеты → откат к Single Point Correction (Глава 4)
                    nearest = min(
                        [c for c in [nearest_before, nearest_after] if c],
                        key=lambda x: abs(x['idx'] - b['idx'])
                    )
                    b['f_drift'][el] = nearest['factor']
                    b['drift_note'][el] = f"SinglePt({nearest['target']})"
            elif nearest_before:
                b['f_drift'][el] = nearest_before['factor']
                b['drift_note'][el] = f"SinglePt({nearest_before['target']})"
            elif nearest_after:
                b['f_drift'][el] = nearest_after['factor']
                b['drift_note'][el] = f"SinglePt({nearest_after['target']})"
            else:
                b['f_drift'][el] = 1.0
                b['drift_note'][el] = "No Bracket"
    
    # === ШАГ 3: Расчёт среднего бланка (ИСПРАВЛЕННАЯ ЛОГИКА) ===
    # ИСПРАВЛЕНИЕ: Проверяем значение бланка против LOQ (SD×10), а не ищем "<" в строке
    avg_blanks = {}
    for el in elements:
        valid_blanks = []
        for b in blocks:
            t = str(b['Type']).upper()
            if any(x in t for x in ['BLK', 'MBB', 'REAGENT']):
                blank_val = to_num(b['avg'][el])
                blank_sd = to_num(b['sd'][el]) or 0.0
                blank_mql = to_num(b['mql'][el]) or 0.0
                
                # 🔍 РАСЧЁТ LOQ ДЛЯ БЛАНКА: max(MQL, SD×10)
                loq_for_blank = max(blank_mql, blank_sd * 10)
                
                # ✅ Если бланк ниже своего LOQ — исключаем из расчета
                if blank_val is not None and blank_val >= loq_for_blank:
                    f = b['f_drift'].get(el, 1.0)
                    valid_blanks.append(blank_val * f)
        
        # Если валидных бланков нет — среднее равно 0
        avg_blanks[el] = np.mean(valid_blanks) if valid_blanks else 0.0
    
    # === ШАГ 4: Генерация трёх таблиц вывода ===
    t1_rows, t2_rows, t3_rows = [], [], []
    
    for b in blocks:
        # ── TABLE 1: Пороги обнаружения и LOQ ──
        row1 = {'Label': b['Label'], 'Type': b['Type']}
        loq_flags = {}
        
        for el in elements:
            raw_v = to_num(b['avg'][el])
            mql_v = to_num(b['mql'][el]) or 0.0
            sd_v = to_num(b['sd'][el]) or 0.0
            loq_threshold = max(mql_v, sd_v * 10)  # Более консервативный порог
            
            is_below_loq = (raw_v is None) or (raw_v < loq_threshold) or ('<' in str(b['avg'][el]))
            
            if is_below_loq:
                row1[el] = f"<{loq_threshold:.4f}"
                loq_flags[el] = loq_threshold  # Запоминаем для Hard Lock
            else:
                rsd_v = to_num(b['rsd'][el]) or 0.0
                flag = "!!" if rsd_v > rsd_h else ("!" if rsd_v > rsd_l else "")
                row1[el] = f"{raw_v:.4f}{flag}"
                loq_flags[el] = None
        t1_rows.append(row1)
        
        # ── TABLES 2 & 3: Только для образцов (тип начинается с 'S') ──
        if str(b['Type']).startswith('S'):
            row2 = {'Label': b['Label']}  # Финальные результаты
            row3 = {'Label': b['Label']}  # Мат-лог (аудит)
            dilution = get_target(b['Type']) or 1.0
            
            for el in elements:
                if loq_flags[el] is not None:
                    # 🔒 HARD LOCK: Ниже LOQ — только разбавление, без дрейфа и бланка
                    row2[el] = f"<{loq_flags[el] * dilution:.4f}"
                    row3[el] = f"LOQ<{loq_flags[el]:.4f} × Dil{dilution} [LOCKED]"
                else:
                    # 📐 Полная формула: ((Raw × Drift) − Blank) × Dilution
                    v_raw = to_num(b['avg'][el])
                    f_drift = b['f_drift'].get(el, 1.0)
                    blank_avg = avg_blanks[el]
                    
                    final_val = ((v_raw * f_drift) - blank_avg) * dilution
                    row2[el] = f"{final_val:.4f}"
                    
                    # Детальный лог расчёта
                    note = b['drift_note'].get(el, 'N/A')
                    qc_mark = "[QC FAIL] " if b['qc_fail'].get(el, False) else ""
                    row3[el] = f"{qc_mark}(({v_raw:.3f}×{f_drift:.3f}[{note}])−{blank_avg:.3f}[BLK])×{dilution}"
            
            t2_rows.append(row2)
            t3_rows.append(row3)
    
    st.session_state.results = (
        pd.DataFrame(t1_rows),
        pd.DataFrame(t2_rows),
        pd.DataFrame(t3_rows)
    )

# ==================== 4. ВЫВОД И ЭКСПОРТ ====================
if st.session_state.results:
    t1, t2, t3 = st.session_state.results
    
    # 📥 Excel-экспорт с разметкой
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        t1.to_excel(writer, sheet_name='Report', startrow=1, index=False)
        t2.to_excel(writer, sheet_name='Report', startrow=len(t1)+5, index=False)
        t3.to_excel(writer, sheet_name='Report', startrow=len(t1)+len(t2)+9, index=False)
        ws = writer.sheets['Report']
        ws.write(0, 0, "TABLE 1: Detection Thresholds")
        ws.write(len(t1)+4, 0, "TABLE 2: Final Results")
        ws.write(len(t1)+len(t2)+8, 0, "TABLE 3: Audit Trail")
    
    st.download_button("📥 Download XLSX Report", buffer.getvalue(), "ElementaQ_Report.xlsx")
    
    # 🖥️ Отображение в Streamlit
    with st.expander("📋 Table 1: Thresholds & LOQ", expanded=True):
        st.dataframe(t1, use_container_width=True, hide_index=True)
    
    with st.expander("✅ Table 2: Final Results", expanded=True):
        st.dataframe(t2, use_container_width=True, hide_index=True)
    
    with st.expander("🔍 Table 3: Math Log (Audit Trail)"):
        st.dataframe(t3, use_container_width=True, hide_index=True)
        st.caption("Format: ((Raw×Factor[Note])−Blank[BLK])×Dilution")
    
    # 📊 QC Summary
    st.markdown("---")
    col1, col2, col3 = st.columns(3)
    with col1:
        yellow = sum(1 for c in t1.columns if c not in ['Label','Type'] 
                    for v in t1[c] if '!' in str(v) and '!!' not in str(v))
        st.metric("🟡 Warnings", yellow)
    with col2:
        red = sum(1 for c in t1.columns if c not in ['Label','Type'] 
                 for v in t1[c] if '!!' in str(v))
        st.metric("🔴 High RSD", red)
    with col3:
        fail = sum(1 for b in blocks for el in elements if b['qc_fail'].get(el, False))
        st.metric("⚠️ QC FAIL", fail)
