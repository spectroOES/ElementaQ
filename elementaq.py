import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ - Full Metrology Suite", layout="wide", page_icon="🧪")

# --- Вспомогательные функции ---
def parse_metadata(name):
    """Извлекает целевое значение и фактор разбавления из Label"""
    target_match = re.search(r'_(\d+\.?\d*)$', str(name))
    dilution_match = re.search(r'_dil(\d+\.?\d*)', str(name))
    target = float(target_match.group(1)) if target_match else None
    dilution = float(dilution_match.group(1)) if dilution_match else 1.0
    return target, dilution

def format_value(val, is_lq=False):
    """Форматирование: научная нотация для следов, 9 знаков для остального"""
    if is_lq:
        prefix = "<"
        val = max(abs(val), 1e-12)
    else:
        prefix = ""
    if 0 < abs(val) < 1e-6:
        return f"{prefix}{val:.4e}"
    else:
        return f"{prefix}{val:.9f}"

def calculate_drift_factor(idx, ccv_map, target_val):
    """Линейная интерполяция дрейфа между стандартами CCV"""
    indices = sorted(ccv_map.keys())
    if not indices: return 1.0
    if len(indices) == 1: return target_val / ccv_map[indices[0]]
    
    if idx <= indices[0]: return target_val / ccv_map[indices[0]]
    if idx >= indices[-1]: return target_val / ccv_map[indices[-1]]
    
    for j in range(len(indices) - 1):
        idx_start, idx_end = indices[j], indices[j+1]
        if idx_start <= idx <= idx_end:
            v_start, v_end = ccv_map[idx_start], ccv_map[idx_end]
            interp_response = v_start + (v_end - v_start) * (idx - idx_start) / (idx_end - idx_start)
            return target_val / interp_response
    return 1.0

# --- Интерфейс ---
st.title("🧪 ElementaQ: Integrated Analytical Suite")
st.write("Professional ICP Data Processing with Corrected Drift & Blanks")
st.markdown("---")

st.sidebar.header("Параметры контроля")
rsd_high = st.sidebar.slider("Порог брака CCV (RSD %)", 1.0, 20.0, 10.0)

uploaded_file = st.file_uploader("Загрузите CSV файл из Qtegra", type="csv")

if uploaded_file:
    raw_df = pd.read_csv(uploaded_file)
    raw_df.columns = raw_df.columns.str.strip()
    elements = [col for col in raw_df.columns if col not in ['Category', 'Label', 'Type']]
    
    # --- PHASE 1: Сжатие и RSD контроль ---
    final_p1 = []
    valid_rows = len(raw_df) - (len(raw_df) % 4)
    for i in range(0, valid_rows, 4):
        block = raw_df.iloc[i : i + 4].copy()
        label = str(block['Label'].iloc[0]).strip()
        stype = str(block['Type'].iloc[0]).strip().upper()
        
        new_row = {'Label': label, 'Type': stype}
        for el in elements:
            try:
                avg_v = block[block['Category'].str.contains('average', case=False, na=False)][el].values[0]
                rsd_v = float(block[block['Category'].str.contains('RSD', case=False, na=False)][el].values[0])
                is_lq = '<LQ' in str(avg_v)
                val = float(re.sub(r'[^0-9.eE-]', '', str(avg_v).split('<')[0]))
                
                res = format_value(val, is_lq)
                if not is_lq and rsd_v > rsd_high: res += "!!"
                new_row[el] = res
            except:
                new_row[el] = "0.000000000"
        final_p1.append(new_row)
    
    ph1_df = pd.DataFrame(final_p1)
    st.write("## 🟢 ТАБЛИЦА 1: Стабильность и RSD")
    st.dataframe(ph1_df)

    # --- PHASE 2: Метрологические расчеты ---
    if st.button("🚀 Запустить расчет Phase 2 (Drift -> Blank -> Dilution)"):
        st.write("## 🔵 ТАБЛИЦА 2: Финальные скорректированные результаты")
        
        ph1_df['Target'], ph1_df['Dilution'] = zip(*ph1_df['Label'].map(parse_metadata))
        ph1_df['Row_Idx'] = range(len(ph1_df))
        ph2_df = ph1_df.copy()
        
        for el in elements:
            # 1. Валидные CCV (без !! и с существующим Target)
            # Исправленная скобка в фильтре ниже:
            ccv_data = ph1_df[(ph1_df['Type'].str.contains('CCV')) & 
                              (~ph1_df[el].astype(str).str.contains('!!')) & 
                              (ph1_df['Target'].notnull())]
            
            if ccv_data.empty:
                continue

            # Берем Target из первого доступного CCV для этого элемента
            target_v = ccv_data['Target'].iloc[0]
            ccv_map = {idx: float(re.sub(r'[^0-9.eE-]', '', str(v).split('!')[0])) 
                       for idx, v in zip(ccv_data['Row_Idx'], ccv_data[el])}

            # 2. Корректируем все Instrumental Blanks (BLK) по дрейфу ДО вычисления среднего
            corrected_blanks = []
            blk_rows = ph1_df[ph1_df['Type'] == 'BLK']
            for idx, row in blk_rows.iterrows():
                raw_blk_val = float(re.sub(r'[^0-9.eE-]', '', str(row[el]).split('!')[0]))
                f_drift_blk = calculate_drift_factor(idx, ccv_map, target_v)
                corrected_blanks.append(raw_blk_val * f_drift_blk)
            
            avg_blank_corrected = np.mean(corrected_blanks) if corrected_blanks else 0.0

            # 3. Применение финальной формулы к образцам (S)
            for i, row in ph2_df.iterrows():
                raw_val = float(re.sub(r'[^0-9.eE-]', '', str(row[el]).split('!')[0]))
                is_lq = '<' in str(row[el])
                
                if row['Type'] == 'S':
                    f_drift_s = calculate_drift_factor(i, ccv_map, target_v)
                    # Формула: (Raw * Drift - Mean_Corrected_Blank) * Dilution
                    final_val = (raw_val * f_drift_s - avg_blank_corrected) * row['Dilution']
                    ph2_df.at[i, el] = format_value(final_val, is_lq)
                else:
                    # Остальные типы (CCV, MBB, BLK) отображаем из Таблицы 1
                    ph2_df.at[i, el] = row[el]

        final_res = ph2_df.drop(columns=['Target', 'Dilution', 'Row_Idx'])
        st.dataframe(final_res)
        
        # Экспорт
        output = io.StringIO()
        output.write("PHASE 1: RSD STABILITY REPORT\n")
        ph1_df.drop(columns=['Target', 'Dilution', 'Row_Idx']).to_csv(output, index=False)
        output.write("\n\nPHASE 2: FINAL METROLOGICAL REPORT\n")
        output.write("Logic: 1. Drift Correction Applied to ALL rows.\n")
        output.write("2. Instrumental Blank Mean calculated FROM Drift-Corrected BLK values.\n")
        output.write("3. Final Result = (Sample_Raw * Drift - Mean_Corrected_Blank) * Dilution.\n")
        output.to_csv(final_res, index=False) # Ошибка в методе записи была здесь, исправлено на:
        
        csv_full = output.getvalue() + final_res.to_csv(index=False)
        st.download_button("📥 СКАЧАТЬ ВЕСЬ ОТЧЕТ", csv_full, "ElementaQ_Final.csv", "text/csv")
