import streamlit as st
import pandas as pd
import io
import re

# --- Конфигурация ElementaQ ---
APP_NAME = "ElementaQ"
st.set_page_config(page_title=APP_NAME, page_icon="🧪", layout="wide")

st.title(f"🧪 {APP_NAME}")
st.subheader("ICP-OES Data Processing Engine")

# --- SIDEBAR: ВСЕ ЛИМИТЫ ---
st.sidebar.header("⚙️ Methodology Settings")

with st.sidebar.expander("RSD & Reporting Limits", expanded=True):
    rsd_limit_low = st.sidebar.slider("Yellow Flag (!) limit (%)", 1.0, 15.0, 6.0, 0.5)
    rsd_limit_high = st.sidebar.slider("Red Flag (!!) limit (%)", 5.0, 30.0, 10.0, 0.5)

with st.sidebar.expander("CCV & Drift Correction", expanded=True):
    ccv_deadband = st.sidebar.number_input("No correction if drift < (%)", 0.0, 5.0, 5.0)
    ccv_max_limit = st.sidebar.number_input("Fail CCV if drift > (%)", 5.0, 20.0, 10.0)
    mismatch_limit = st.sidebar.number_input("Sample/CCV Mismatch limit (%)", 5.0, 50.0, 20.0)

# --- Вспомогательные функции ---
def clean_numeric(val):
    if pd.isna(val): return 0.0
    if isinstance(val, str):
        if "<" in val or "n/a" in val: return 0.0
        val = re.sub(r'[!!|!]', '', val)
    try: return float(val)
    except: return 0.0

def extract_target(label):
    # Извлекает концентрацию из имени типа MixI_10
    match = re.search(r'[_ ]([\d\.]+)$', str(label))
    return float(match.group(1)) if match else None

# --- ЗАГРУЗКА ФАЙЛА ---
uploaded_file = st.file_uploader("Upload ICP-OES CSV", type="csv")

if uploaded_file:
    df_raw = pd.read_csv(uploaded_file)
    element_cols = [col for col in df_raw.columns if col not in ['Category', 'Label', 'Type']]
    
    # --- ЭТАП 1 ---
    if st.button("🚀 Run Full Processing"):
        # 1. Сбор данных Этапа 1 (Фильтрация и RSD)
        processed_s1 = []
        for i in range(0, len(df_raw), 4):
            if i + 3 >= len(df_raw): break
            block = df_raw.iloc[i : i + 4].copy()
            label, row_type = str(block['Label'].iloc[0]), str(block['Type'].iloc[0])
            new_row = {'Label': label, 'Type': row_type}
            for el in element_cols:
                avg_v = clean_numeric(block[block['Category'].str.strip() == "Concentration average"][el].values[0])
                sd_v = clean_numeric(block[block['Category'].str.strip() == "Concentration SD"][el].values[0])
                rsd_v = clean_numeric(block[block['Category'].str.strip() == "Concentration RSD"][el].values[0])
                matrix_mql = sd_v * 10
                if avg_v < matrix_mql: new_row[el] = f"<{round(matrix_mql, 4)}"
                else:
                    flag = "!!" if rsd_v > rsd_limit_high else ("!" if rsd_v > rsd_limit_low else "")
                    new_row[el] = f"{round(avg_v, 4)}{flag}"
            processed_s1.append(new_row)
        df_s1 = pd.DataFrame(processed_s1)

        # 2. Этап 2 и Генерация ЛОГА (Table 3)
        blank_rows = df_s1[df_s1['Type'] == 'BLK']
        avg_blanks = {el: blank_rows[el].apply(clean_numeric).mean() if not blank_rows.empty else 0.0 for el in element_cols}
        
        final_results = []
        log_entries = []
        drift_factors = {el: 1.0 for el in element_cols}

        for _, row in df_s1.iterrows():
            res_row = row.to_dict()
            
            # Обновление дрейфа по CCV
            if "CCV" in str(row['Type']):
                target = extract_target(row['Label'])
                if target:
                    for el in element_cols:
                        measured = clean_numeric(row[el])
                        if measured > 0:
                            drift_err = abs((measured - target) / target) * 100
                            if drift_err <= ccv_deadband: drift_factors[el] = 1.0
                            elif drift_err <= ccv_max_limit: drift_factors[el] = target / measured
                            else: drift_factors[el] = 1.0 # FAIL - не корректируем

            # Расчет для проб (S)
            if row['Type'] == 'S':
                # Разбавление (если есть в имени _dilXX)
                dil_match = re.search(r'_dil(\d+)', row['Label'])
                df_val = float(dil_match.group(1)) if dil_match else 1.0
                
                for el in element_cols:
                    raw_val = clean_numeric(row[el])
                    f_drift = drift_factors[el]
                    c_blank = avg_blanks.get(el, 0)
                    
                    # Математика: (Raw * Drift - Blank) * Dilution
                    corrected = (raw_val * f_drift) - c_blank
                    final_v = max(0, corrected) * df_val
                    res_row[el] = round(final_v, 4)

                    # Запись в ЛОГ
                    status = "PASS"
                    if f_drift != 1.0: status = "Drift Corr."
                    if raw_val > 0 and abs((raw_val - target)/target if 'target' in locals() else 0) > (mismatch_limit/100):
                        status += " | Mismatch"

                    log_entries.append({
                        'Sample': row['Label'],
                        'Element': el,
                        'C_raw': raw_val,
                        'f_Drift': round(f_drift, 3),
                        'C_blank': round(c_blank, 5),
                        'Dilution': df_val,
                        'Final_Result': round(final_v, 4),
                        'Status': status
                    })
            final_results.append(res_row)

        # Сохранение в сессию для отображения
        st.session_state['df_table2'] = pd.DataFrame(final_results)
        st.session_state['df_log'] = pd.DataFrame(log_entries)

    # --- ОТОБРАЖЕНИЕ РЕЗУЛЬТАТОВ ---
    if 'df_table2' in st.session_state:
        st.write("### Table 2: Final Corrected Results")
        st.dataframe(st.session_state['df_table2'], use_container_width=True)

        st.divider()
        st.write("### Table 3: Calculation Log & Validation")
        st.info("This table explains how each value was derived (Drift -> Blank -> Dilution).")
        st.dataframe(st.session_state['df_log'], use_container_width=True)

        # Скачивание лога
        log_csv = st.session_state['df_log'].to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Log (Table 3)", log_csv, "ElementaQ_Log.csv", "text/csv")
