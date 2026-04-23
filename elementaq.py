import streamlit as st
import pandas as pd
import re
import numpy as np

# --- 1. ИНИЦИАЛИЗАЦИЯ И ИНТЕРФЕЙС ---
st.set_page_config(page_title="ElementaQ Pro", layout="wide")
st.title("🧪 ElementaQ: Analytical Engine")

# Сайдбар (всегда отрисовывается первым)
st.sidebar.header("⚙️ Methodology Settings")
ccv_deadband = st.sidebar.number_input("No correction if drift < (%)", 0.0, 5.0, 5.0, key='deadband')
ccv_max_limit = st.sidebar.number_input("Fail CCV if drift > (%)", 5.0, 50.0, 20.0, key='max_limit')

# --- 2. ЯДРО ЛОГИКИ ---
def clean_val(val):
    if pd.isna(val): return 0.0
    if isinstance(val, str):
        val = re.sub(r'[^\d\.]', '', val.split('<')[0])
    try: return float(val)
    except: return 0.0

def get_target(type_str):
    """Извлекает номинал из CCV_0.1 -> 0.1"""
    match = re.search(r'_([\d\.]+)$', str(type_str))
    return float(match.group(1)) if match else None

# --- 3. ЗАГРУЗКА И ОБРАБОТКА ---
file = st.file_uploader("Upload ICP-OES CSV", type="csv")

if file:
    df_raw = pd.read_csv(file)
    elements = [c for c in df_raw.columns if c not in ['Category', 'Label', 'Type']]
    
    if st.button("🚀 Execute Analysis"):
        # ШАГ 1: Агрегация (Table 1)
        data_s1 = []
        for i in range(0, len(df_raw), 4):
            if i + 3 >= len(df_raw): break
            chunk = df_raw.iloc[i : i+4]
            label = str(chunk['Label'].iloc[0])
            rtype = str(chunk['Type'].iloc[0])
            row = {'Label': label, 'Type': rtype}
            for el in elements:
                row[el] = clean_val(chunk[chunk['Category'].str.strip() == "Concentration average"][el].values[0])
            data_s1.append(row)
        df_s1 = pd.DataFrame(data_s1)

        # ШАГ 2: ПЕРВЫЙ ПРОХОД (Расчет факторов дрейфа для всей сессии)
        drift_factors = {el: [] for el in elements}
        for _, row in df_s1.iterrows():
            target = get_target(row['Type'])
            if "CCV" in str(row['Type']) and target:
                for el in elements:
                    measured = row[el]
                    if measured > 0:
                        factor = target / measured
                        drift_err = abs((measured - target) / target) * 100
                        if drift_err > ccv_deadband and drift_err <= ccv_max_limit:
                            drift_factors[el].append(factor)
        
        # Усредняем факторы (если CCV было несколько)
        final_drift = {}
        for el in elements:
            if drift_factors[el]:
                final_drift[el] = np.mean(drift_factors[el])
            else:
                final_drift[el] = 1.0

        # ШАГ 3: ВТОРОЙ ПРОХОД (Расчет результатов с финальными факторами)
        avg_blanks = {el: df_s1[df_s1['Type'] == 'BLK'][el].mean() if not df_s1[df_s1['Type'] == 'BLK'].empty else 0.0 for el in elements}
        
        table2, table3 = [], []
        for _, row in df_s1.iterrows():
            rtype, label = str(row['Type']), str(row['Label'])
            t2_row, t3_row = {'Label': label, 'Type': rtype}, {'Label': label}
            
            dil = 1.0
            dil_match = re.search(r'_dil(\d+)', label)
            if dil_match: dil = float(dil_match.group(1))

            for el in elements:
                raw = row[el]
                f = final_drift[el]
                # Бланк только для S
                blk = avg_blanks.get(el, 0) if rtype == 'S' else 0.0
                
                res = round(max(0, (raw * f) - blk) * dil, 4)
                t2_row[el] = res
                
                # Запись в аудит (только для S, MBB, BLK)
                if rtype in ['S', 'MBB', 'BLK']:
                    t3_row[el] = f"({raw}*{f:.3f}-{blk:.3f})*{int(dil)}"
            
            table2.append(t2_row)
            if rtype in ['S', 'MBB', 'BLK']:
                table3.append(t3_row)

        st.session_state['out'] = (df_s1, pd.DataFrame(table2), pd.DataFrame(table3))

    # --- 4. ВЫВОД (Гарантирует сохранение таблиц) ---
    if 'out' in st.session_state:
        s1, s2, s3 = st.session_state['out']
        
        tabs = st.tabs(["📊 Table 1 (Raw)", "✅ Table 2 (Results)", "🔍 Table 3 (Audit)"])
        
        with tabs[0]:
            st.dataframe(s1, use_container_width=True)
        with tabs[1]:
            st.dataframe(s2, use_container_width=True)
        with tabs[2]:
            st.info(f"Drift factors applied: { {k: round(v,3) for k,v in final_drift.items() if v != 1.0} }")
            st.dataframe(s3, use_container_width=True)
