import streamlit as st
import pandas as pd
import re

# --- 1. ИНТЕРФЕЙС (Всегда активен) ---
st.set_page_config(page_title="ElementaQ", layout="wide")
st.title("🧪 ElementaQ: Trace Analysis Engine")

# Сайдбар вынесен в корень, чтобы бегунки не исчезали
st.sidebar.header("⚙️ Methodology Settings")
ccv_deadband = st.sidebar.number_input("No correction if drift < (%)", 0.0, 5.0, 5.0)
ccv_max_limit = st.sidebar.number_input("Fail CCV if drift > (%)", 5.0, 30.0, 20.0)

# --- 2. ФУНКЦИИ ---
def clean_numeric(val):
    if pd.isna(val): return 0.0
    if isinstance(val, str):
        val = re.sub(r'[^\d\.]', '', val.split('<')[0])
    try: return float(val)
    except: return 0.0

def extract_target_from_type(type_val):
    # Ищем число после подчеркивания в Type (например, CCV_0.1)
    match = re.search(r'_([\d\.]+)$', str(type_val))
    return float(match.group(1)) if match else None

# --- 3. ОБРАБОТКА ФАЙЛА ---
uploaded_file = st.file_uploader("Upload ICP-OES CSV", type="csv")

if uploaded_file:
    df_raw = pd.read_csv(uploaded_file)
    element_cols = [col for col in df_raw.columns if col not in ['Category', 'Label', 'Type']]
    
    if st.button("🚀 Run Analysis"):
        # Шаг 1: Агрегация из сырого файла (по 4 строки)
        processed_s1 = []
        for i in range(0, len(df_raw), 4):
            if i + 3 >= len(df_raw): break
            block = df_raw.iloc[i : i + 4].copy()
            label, row_type = str(block['Label'].iloc[0]), str(block['Type'].iloc[0])
            new_row = {'Label': label, 'Type': row_type}
            for el in element_cols:
                avg = clean_numeric(block[block['Category'].str.strip() == "Concentration average"][el].values[0])
                new_row[el] = avg
            processed_s1.append(new_row)
        df_s1 = pd.DataFrame(processed_s1)

        # Шаг 2: Расчет аналитического бланка (BLK)
        blank_rows = df_s1[df_s1['Type'] == 'BLK']
        avg_blanks = {el: blank_rows[el].mean() if not blank_rows.empty else 0.0 for el in element_cols}
        
        table2_data = []
        table3_data = []
        drift_factors = {el: 1.0 for el in element_cols}

        for _, row in df_s1.iterrows():
            row_type = str(row['Type'])
            label = str(row['Label'])
            
            # --- ФИКС: Сначала обновляем дрейф, потом считаем строку ---
            target = extract_target_from_type(row_type)
            if "CCV" in row_type and target:
                for el in element_cols:
                    measured = row[el]
                    if measured > 0:
                        err = abs((measured - target) / target) * 100
                        # Если дрейф реальный, обновляем фактор СРАЗУ
                        if err > ccv_deadband and err <= ccv_max_limit:
                            drift_factors[el] = target / measured
                        else:
                            drift_factors[el] = 1.0

            # Логика разбавления (только из Label)
            dil_match = re.search(r'_dil(\d+)', label)
            df_val = float(dil_match.group(1)) if dil_match else 1.0
            
            t2_row = {'Label': label, 'Type': row_type}
            t3_row = {'Label': label}
            
            for el in element_cols:
                raw_val = row[el]
                f = drift_factors[el]
                
                # Вычитаем бланк только из S (Sample). Из CCV, MBB и BLK - нет.
                blk = avg_blanks.get(el, 0) if row_type == 'S' else 0.0
                
                # Итоговый расчет
                net_val = (raw_val * f) - blk
                final_res = round(max(0, net_val) * df_val, 4)
                
                t2_row[el] = final_res
                
                # Формула для Table 3 (Аудит)
                f_txt = f"{f:.3f}" if f != 1.0 else "1"
                t3_row[el] = f"({raw_val}*{f_txt}-{blk:.3f})*{int(df_val)}"

            table2_data.append(t2_row)
            # В аудит лог пускаем S, MBB, BLK и теперь CCV (чтобы ты видел, как они поправились)
            if row_type in ['S', 'MBB', 'BLK', 'CCV'] or "CCV" in row_type:
                table3_data.append(t3_row)

        st.session_state['results'] = (df_s1, pd.DataFrame(table2_data), pd.DataFrame(table3_data))

    # --- 4. ВЫВОД РЕЗУЛЬТАТОВ ---
    if 'results' in st.session_state:
        s1, s2, s3 = st.session_state['results']
        st.subheader("1️⃣ Table 1: Instrumental Raw Data")
        st.dataframe(s1, use_container_width=True)
        
        st.subheader("2️⃣ Table 2: Final Results (All Rows)")
        st.dataframe(s2, use_container_width=True)
        
        st.subheader("3️⃣ Table 3: Audit Trail (Calculation Log)")
        st.info("Formula: (Raw * Drift_Factor - Blank) * Dilution. Blank is 0 for Standards/MBB.")
        st.dataframe(s3, use_container_width=True)
