import streamlit as st
import pandas as pd
import re

# --- 1. ИНТЕРФЕЙС И НАСТРОЙКИ ---
st.set_page_config(page_title="ElementaQ", layout="wide")
st.title("🧪 ElementaQ: Trace Analysis Engine")

st.sidebar.header("⚙️ Methodology Settings")
rsd_limit_low = st.sidebar.slider("Yellow Flag (!) RSD %", 1.0, 15.0, 6.0, 0.5)
rsd_limit_high = st.sidebar.slider("Red Flag (!!) RSD %", 5.0, 30.0, 10.0, 0.5)

st.sidebar.markdown("---")
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
    """Цель берется только из колонки Type после подчеркивания"""
    match = re.search(r'_([\d\.]+)$', str(type_val))
    return float(match.group(1)) if match else None

# --- 3. ОБРАБОТКА ---
uploaded_file = st.file_uploader("Upload ICP-OES CSV", type="csv")

if uploaded_file:
    df_raw = pd.read_csv(uploaded_file)
    element_cols = [col for col in df_raw.columns if col not in ['Category', 'Label', 'Type']]
    
    if st.button("🚀 Run Analysis"):
        # Шаг 1: Сбор данных
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

        # Шаг 2: Расчет среднего BLK
        blank_rows = df_s1[df_s1['Type'] == 'BLK']
        avg_blanks = {el: blank_rows[el].mean() if not blank_rows.empty else 0.0 for el in element_cols}
        
        table2_data = []
        table3_data = []
        drift_factors = {el: 1.0 for el in element_cols}

        for _, row in df_s1.iterrows():
            row_type = str(row['Type'])
            label = str(row['Label'])
            
            # Обновление дрейфа по Type
            target = extract_target_from_type(row_type)
            if "CCV" in row_type and target:
                for el in element_cols:
                    measured = row[el]
                    if measured > 0:
                        err = abs((measured - target) / target) * 100
                        drift_factors[el] = target / measured if (err > ccv_deadband and err <= ccv_max_limit) else 1.0

            # Логика разбавления
            dil_match = re.search(r'_dil(\d+)', label)
            df_val = float(dil_match.group(1)) if dil_match else 1.0
            
            t2_row = {'Label': label, 'Type': row_type}
            t3_row = {'Label': label}
            
            for el in element_cols:
                raw_val = row[el]
                f = drift_factors[el]
                
                # --- ИСПРАВЛЕННАЯ ЛОГИКА ВЫЧИТАНИЯ ---
                # Бланк вычитается ТОЛЬКО из проб (S).
                # Из MBB, BLK и CCV вычитание бланка ЗАПРЕЩЕНО (blk = 0).
                blk = avg_blanks.get(el, 0) if row_type == 'S' else 0.0
                
                net_val = (raw_val * f) - blk
                final_res = round(max(0, net_val) * df_val, 4)
                t2_row[el] = final_res
                
                # Формула для Table 3
                f_txt = f"{f:.2f}" if f != 1.0 else "1"
                t3_row[el] = f"({raw_val}*{f_txt}-{blk:.3f})*{int(df_val)}"

            table2_data.append(t2_row)
            if row_type in ['S', 'MBB', 'BLK']:
                table3_data.append(t3_row)

        st.session_state['results'] = (df_s1, pd.DataFrame(table2_data), pd.DataFrame(table3_data))

    # --- 4. ВЫВОД ---
    if 'results' in st.session_state:
        s1, s2, s3 = st.session_state['results']
        st.subheader("1️⃣ Instrumental Raw Data")
        st.dataframe(s1, use_container_width=True)
        st.subheader("2️⃣ Final Results (All Rows)")
        st.dataframe(s2, use_container_width=True)
        st.subheader("3️⃣ Audit Trail (S, MBB, BLK Only)")
        st.info("Note: Blank subtraction is strictly applied ONLY to 'S' type rows.")
        st.dataframe(s3, use_container_width=True)
