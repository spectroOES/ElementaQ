import streamlit as st
import pandas as pd
import io

# --- КОРРЕКТНОЕ НАЗВАНИЕ ---
APP_NAME = "ElementaQ"

st.set_page_config(page_title=APP_NAME, page_icon="🧪", layout="wide")

# --- ИНТЕРФЕЙС ---
st.title(f"🧪 {APP_NAME}")
st.subheader("ICP-OES Data Processing Utility")

# --- SIDEBAR (Пользовательские настройки RSD) ---
st.sidebar.header("RSD Threshold Settings")
rsd_limit_low = st.sidebar.slider(
    "Yellow Flag (!) limit (%)", 
    min_value=1.0, max_value=15.0, value=6.0, step=0.5
)
rsd_limit_high = st.sidebar.slider(
    "Red Flag (!!) limit (%)", 
    min_value=5.0, max_value=30.0, value=10.0, step=0.5
)

# --- ЗАГРУЗКА ФАЙЛА ---
uploaded_file = st.file_uploader("Upload source ICP-OES CSV file", type="csv")

if uploaded_file:
    # Читаем данные только для предварительного анализа колонок
    df_raw = pd.read_csv(uploaded_file)
    
    # Определяем колонки с элементами
    non_element_cols = ['Category', 'Label', 'Type']
    element_cols = [col for col in df_raw.columns if col not in non_element_cols]
    
    st.info(f"File '{uploaded_file.name}' loaded. Press the button below to process {len(element_cols)} elements.")

    # --- КНОПКА ЗАПУСКА (БЕЗ НЕЕ РАСЧЕТ НЕ НАЧИНАЕТСЯ) ---
    if st.button("🚀 Start Calculations"):
        processed_data = []
        last_inst_mql = {} 

        # Обработка блоками по 4 строки
        for i in range(0, len(df_raw), 4):
            if i + 3 >= len(df_raw):
                break
            
            block = df_raw.iloc[i : i + 4].copy()
            block['Category'] = block['Category'].str.strip()
            
            # Извлекаем метаданные из первой строки блока
            label = str(block['Label'].iloc[0])
            row_type = str(block['Type'].iloc[0])
            
            new_row = {
                'Label': label,
                'Type': row_type
            }

            for el in element_cols:
                try:
                    # Фильтруем значения внутри блока по категории
                    avg_val_raw = block[block['Category'] == "Concentration average"][el].values[0]
                    sd_val = float(block[block['Category'] == "Concentration SD"][el].values[0])
                    rsd_val = float(block[block['Category'] == "Concentration RSD"][el].values[0])
                    inst_mql = float(block[block['Category'] == "MQL"][el].values[0])
                    
                    # Сохраняем приборный MQL для справочной строки
                    last_inst_mql[el] = inst_mql

                    # Формула Matrix LOQ: SD * 10
                    matrix_mql = sd_val * 10
                    
                    is_below = False
                    # Проверка на строковое "<LQ" или значение ниже матричного порога
                    if isinstance(avg_val_raw, str) and "<LQ" in avg_val_raw:
                        is_below = True
                    else:
                        num_avg = float(avg_val_raw)
                        if num_avg < matrix_mql:
                            is_below = True
                    
                    if is_below:
                        new_row[el] = f"<{round(matrix_mql, 4)}"
                    else:
                        # Логика флагов RSD (из настроек в Sidebar)
                        if rsd_val > rsd_limit_high:
                            new_row[el] = f"{round(num_avg, 4)}!!"
                        elif rsd_val > rsd_limit_low:
                            new_row[el] = f"{round(num_avg, 4)}!"
                        else:
                            new_row[el] = round(num_avg, 4)
                            
                except (ValueError, IndexError, TypeError):
                    new_row[el] = "n/a"

            processed_data.append(new_row)

        # Создаем итоговую таблицу
        res_df = pd.DataFrame(processed_data)

        # Добавляем строку Instrumental MQL в конец
        if last_inst_mql:
            mql_ref_row = {'Label': 'MQL (Instrument)', 'Type': 'REF'}
            mql_ref_row.update(last_inst_mql)
            res_df = pd.concat([res_df, pd.DataFrame([mql_ref_row])], ignore_index=True)

        # --- ОТОБРАЖЕНИЕ И СКАЧИВАНИЕ ---
        st.divider()
        st.success("Calculations complete!")
        st.write(f"### Result Table 1 (Matrix MQL & RSD Flags)")
        st.dataframe(res_df, use_container_width=True)

        # Подготовка CSV для экспорта
        csv_buffer = io.StringIO()
        res_df.to_csv(csv_buffer, index=False)
        csv_data = csv_buffer.getvalue()

        st.download_button(
            label="📥 Download Result CSV",
            data=csv_data,
            file_name="ElementaQ_Table1.csv",
            mime="text/csv"
        )
