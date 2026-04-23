import streamlit as st
import pandas as pd
import re
import io
import numpy as np

# --- 1. ИНИЦИАЛИЗАЦИЯ ИНТЕРФЕЙСА (Всегда в начале) ---
st.set_page_config(page_title="ElementaQ Pro", layout="wide")
st.title("🧪 ElementaQ: Analytical Engine v6.0")

# Виджеты настроек в Sidebar (Бессмертные)
with st.sidebar:
    st.header("⚙️ Контроль качества")
    rsd_l = st.slider("Yellow Flag RSD %", 1.0, 15.0, 6.0, key='rsd_l')
    rsd_h = st.slider("Red Flag RSD %", 5.0, 30.0, 10.0, key='rsd_h')
    st.markdown("---")
    st.header("📈 Лимиты дрейфа")
    db = st.number_input("Мертвая зона (%)", 0.0, 10.0, 5.0, key='db')
    ml = st.number_input("Макс. коррекция (%)", 5.0, 50.0, 20.0, key='ml')

# --- 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def clean_num(val):
    if pd.isna(val): return 0.0
    if isinstance(val, str):
        val = re.sub(r'[^\d\.]', '', val.split('<')[0])
    try: return float(val)
    except: return 0.0

def get_nominal_from_type(t_str):
    """Берет число после подчеркивания (CCV_0.1 -> 0.1)"""
    res = re.search(r'_([\d\.]+)$', str(t_str))
    return float(res.group(1)) if res else None

# --- 3. ОБРАБОТКА ---
uploaded_file = st.file_uploader("Загрузите CSV файл", type="csv")

if uploaded_file:
    df_raw = pd.read_csv(uploaded_file)
    # Колонки с данными - это всё, кроме служебных
    data_cols = [c for c in df_raw.columns if c not in ['Category', 'Label', 'Type']]
    
    # Кнопка запуска
    run_calc = st.button("🚀 Выполнить расчет")

    if run_calc:
        # ШАГ 1: Агрегация (Table 1) - по 4 строки
        rows_s1 = []
        for i in range(0, len(df_raw), 4):
            if i + 3 >= len(df_raw): break
            chunk = df_raw.iloc[i : i+4]
            label = str(chunk['Label'].iloc[0])
            rtype = str(chunk['Type'].iloc[0])
            
            new_row = {'Label': label, 'Type': rtype}
            for col in data_cols:
                # Извлекаем значение из строки "Concentration average"
                val = clean_num(chunk[chunk['Category'].str.strip() == "Concentration average"][col].values[0])
                new_row[col] = val
            rows_s1.append(new_row)
        
        df_s1 = pd.DataFrame(rows_s1)

        # ШАГ 2: Расчет коэффициентов дрейфа (f)
        # Собираем все CCV и считаем средний f для каждой колонки
        drift_factors = {col: [] for col in data_cols}
        for _, row in df_s1.iterrows():
            target = get_nominal_from_type(row['Type'])
            if "CCV" in str(row['Type']) and target:
                for col in data_cols:
                    measured = row[col]
                    if measured > 0:
                        error = abs((measured - target) / target) * 100
                        if db < error <= ml:
                            drift_factors[col].append(target / measured)
        
        # Усредняем факторы (если нет CCV или ошибка мала, f = 1.0)
        final_f = {col: (np.mean(factors) if factors else 1.0) for col, factors in drift_factors.items()}

        # ШАГ 3: Финальный расчет и формирование таблиц 2 и 3
        # Средний бланк по всей серии
        avg_blks = {col: df_s1[df_s1['Type'] == 'BLK'][col].mean() if not df_s1[df_s1['Type'] == 'BLK'].empty else 0.0 for col in data_cols}
        
        table2_rows = []
        table3_rows = []

        for _, row in df_s1.iterrows():
            rtype = str(row['Type'])
            label = str(row['Label'])
            
            t2_row = {'Label': label, 'Type': rtype}
            t3_row = {'Label': label}
            
            # Разбавление (из Label)
            dil = 1
            if '_dil' in label:
                m = re.search(r'_dil(\d+)', label)
                if m: dil = int(m.group(1))

            for col in data_cols:
                raw_val = row[col]
                f = final_f[col]
                
                # Бланк вычитается ТОЛЬКО из S. Из CCV, BLK, MBB - нет.
                blk = avg_blks.get(col, 0) if rtype == 'S' else 0.0
                
                # Итоговая формула
                res = round(max(0, (raw_val * f) - blk) * dil, 4)
                t2_row[col] = res
                
                # Лог расчета (только S, MBB, BLK)
                if rtype in ['S', 'MBB', 'BLK']:
                    f_str = f"{f:.3f}" if f != 1.0 else "1"
                    t3_row[col] = f"({raw_val}*{f_str}-{blk:.3f})*{dil}"

            table2_rows.append(t2_row)
            if rtype in ['S', 'MBB', 'BLK']:
                table3_rows.append(t3_row)

        st.session_state['processed_data'] = (df_s1, pd.DataFrame(table2_rows), pd.DataFrame(table3_rows), final_f)

    # --- 4. ВЫВОД (Вне условий, чтобы не пропадало) ---
    if 'processed_data' in st.session_state:
        s1, s2, s3, f_applied = st.session_state['processed_data']
        
        # Генерация Excel
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            s1.to_excel(writer, sheet_name='1_Raw', index=False)
            s2.to_excel(writer, sheet_name='2_Final', index=False)
            s3.to_excel(writer, sheet_name='3_Audit', index=False)
        
        st.download_button(
            label="📥 Download Excel Report",
            data=output.getvalue(),
            file_name="ElementaQ_Report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        # Таблицы на вкладках
        tab1, tab2, tab3 = st.tabs(["📊 Table 1: Raw", "✅ Table 2: Final", "🔍 Table 3: Audit"])
        
        with tab1:
            st.dataframe(s1, use_container_width=True)
        with tab2:
            st.dataframe(s2, use_container_width=True)
        with tab3:
            st.info(f"Примененные коэффициенты (f): { {k: round(v,3) for k,v in f_applied.items() if v != 1.0} }")
            st.dataframe(s3, use_container_width=True)
