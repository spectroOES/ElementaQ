import streamlit as st
import pandas as pd
import io
import re

# --- Настройки ElementaQ ---
st.set_page_config(page_title="ElementaQ", page_icon="🧪", layout="wide")
st.title("🧪 ElementaQ: Trace Analysis Engine")

# --- SIDEBAR (Лимиты) ---
with st.sidebar.expander("Methodology Settings", expanded=True):
    ccv_deadband = st.number_input("No correction < (%)", 0.0, 5.0, 5.0)
    ccv_max_limit = st.number_input("Fail CCV > (%)", 5.0, 20.0, 10.0)

# --- Функции ---
def clean_numeric(val):
    if pd.isna(val): return 0.0
    if isinstance(val, str):
        if "<" in val or "n/a" in val: return 0.0
        val = re.sub(r'[!!|!]', '', val)
    try: return float(val)
    except: return 0.0

def extract_target(label):
    match = re.search(r'[_ ]([\d\.]+)$', str(label))
    return float(match.group(1)) if match else None

# --- Загрузка и Обработка ---
uploaded_file = st.file_uploader("Upload CSV", type="csv")

if uploaded_file:
    df_raw = pd.read_csv(uploaded_file)
    element_cols = [col for col in df_raw.columns if col not in ['Category', 'Label', 'Type']]
    
    if st.button("🚀 Run Full Calculations"):
        # 1. Этап 1 (Table 1)
        processed_s1 = []
        for i in range(0, len(df_raw), 4):
            if i + 3 >= len(df_raw): break
            block = df_raw.iloc[i : i + 4].copy()
            label, row_type = str(block['Label'].iloc[0]), str(block['Type'].iloc[0])
            new_row = {'Label': label, 'Type': row_type}
            for el in element_cols:
                avg_v = clean_numeric(block[block['Category'].str.strip() == "Concentration average"][el].values[0])
                new_row[el] = round(avg_v, 4) # Упростим для теста
            processed_s1.append(new_row)
        df_s1 = pd.DataFrame(processed_s1)

        # 2. Этап 2 и 3 (Results & Audit)
        blank_rows = df_s1[df_s1['Type'] == 'BLK']
        avg_blanks = {el: blank_rows[el].apply(clean_numeric).mean() if not blank_rows.empty else 0.0 for el in element_cols}
        
        table2_data = []
        table3_data = [] # Audit Table
        drift_factors = {el: 1.0 for el in element_cols}

        for _, row in df_s1.iterrows():
            t2_row = {'Label': row['Label']}
            t3_row = {'Label': row['Label']}
            
            # Обновление дрейфа по CCV
            if "CCV" in str(row['Type']):
                target = extract_target(row['Label'])
                if target:
                    for el in element_cols:
                        measured = clean_numeric(row[el])
                        if measured > 0:
                            err = abs((measured - target) / target) * 100
                            drift_factors[el] = (target / measured) if err > ccv_deadband and err <= ccv_max_limit else 1.0

            # Расчет для проб
            if row['Type'] == 'S':
                dil_match = re.search(r'_dil(\d+)', row['Label'])
                df_val = float(dil_match.group(1)) if dil_match else 1.0
                
                for el in element_cols:
                    raw_val = clean_numeric(row[el])
                    f = drift_factors[el]
                    blk = avg_blanks.get(el, 0)
                    
                    # Финальное значение (Table 2)
                    res = round(max(0, (raw_val * f) - blk) * df_val, 4)
                    t2_row[el] = res
                    
                    # Формула аудита (Table 3)
                    # Формат: (Raw * f - Blk) * DF
                    f_str = f"{f:.2f}" if f != 1.0 else "1"
                    t3_row[el] = f"({raw_val}*{f_str}-{blk:.3f})*{int(df_val)}"
            else:
                # Для не-S образцов просто копируем значения
                for el in element_cols:
                    t2_row[el] = row[el]
                    t3_row[el] = "N/A (QC)"

            table2_data.append(t2_row)
            table3_data.append(t3_row)

        st.session_state['df_s1'] = df_s1
        st.session_state['df_s2'] = pd.DataFrame(table2_data)
        st.session_state['df_s3'] = pd.DataFrame(table3_data)
        st.session_state['done'] = True

    # --- ОТОБРАЖЕНИЕ ---
    if st.session_state.get('done'):
        st.subheader("1️⃣ Table 1: Initial Data")
        st.dataframe(st.session_state['df_s1'], use_container_width=True)

        st.subheader("2️⃣ Table 2: Final Results")
        st.dataframe(st.session_state['df_s2'], use_container_width=True)

        st.subheader("3️⃣ Table 3: Calculation Audit Log")
        st.info("Notation: (Measured * Drift_Factor - Analytical_Blank) * Dilution_Factor")
        st.dataframe(st.session_state['df_s3'], use_container_width=True)
