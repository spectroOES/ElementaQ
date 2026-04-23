import pandas as pd
import numpy as np
import streamlit as st
import re
from io import BytesIO

# --- ИНИЦИАЛИЗАЦИЯ ИНТЕРФЕЙСА (ТОЧНО ПО ЦИТАТЕ) ---
st.set_page_config(layout="wide", page_title="ICP Processor")
st.title("🔬 ICP-OES Data Processor")

# Верхняя панель (виджеты)
c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    uploaded_file = st.file_uploader("Upload ICP CSV", type="csv")
with c2:
    process_btn = st.button("🚀 Start Processing", use_container_width=True)
with c3:
    st.write("Status: " + ("Ready" if uploaded_file else "Waiting for file"))

st.markdown("---")

# --- ЛОГИКА ОБРАБОТКИ ---
def extract_target(type_val):
    if pd.isna(type_val): return None
    match = re.search(r'_(\d+\.?\d*)$', str(type_val))
    return float(match.group(1)) if match else None

def get_dilution(label):
    label = str(label)
    if '/' in label:
        try: return float(label.split('/')[-1])
        except: pass
    if '_dil' in label:
        try: return float(label.split('_dil')[-1])
        except: pass
    return 1.0

def run_analytical_engine(df):
    df.columns = [c.strip() for c in df.columns]
    elements = [c for c in df.columns if c not in ['Category', 'Label', 'Type']]
    
    # Группировка строк по 4 (average, SD, RSD, MQL)
    blocks = []
    for i in range(0, len(df), 4):
        block = df.iloc[i:i+4]
        if len(block) < 4: continue
        avg = block[block['Category'].str.contains('average', case=False)].iloc[0]
        sd = block[block['Category'].str.contains('SD', case=False)].iloc[0]
        rsd = block[block['Category'].str.contains('RSD', case=False)].iloc[0]
        blocks.append({'idx': i, 'Label': avg['Label'], 'Type': avg['Type'], 'avg': avg, 'sd': sd, 'rsd': rsd})

    t1_data, t2_data, t3_data = [], [], []

    for b in blocks:
        # Таблица 1 (Thresholds) - Всегда для всех типов
        t1_row = {'Label': b['Label'], 'Type': b['Type']}
        for el in elements:
            val = pd.to_numeric(b['avg'][el], errors='coerce')
            mql = pd.to_numeric(b['sd'][el], errors='coerce') * 10
            rsd_val = pd.to_numeric(b['rsd'][el], errors='coerce')
            if pd.isna(val): t1_row[el] = "N/A"
            elif val < mql: t1_row[el] = f"<{mql:.3f}"
            else:
                flag = "!!" if rsd_val > 10 else ("!" if rsd_val > 6 else "")
                t1_row[el] = f"{val:.4f}{flag}"
        t1_data.append(t1_row)

        # Таблицы 2 и 3 (Только для проб 'S')
        if b['Type'] == 'S':
            t2_row, t3_row = {'Label': b['Label']}, {'Label': b['Label']}
            for el in elements:
                val_raw = pd.to_numeric(b['avg'][el], errors='coerce')
                
                # Поиск CCV по критерию +/- 20%
                f_drift, ccv_used = 1.0, "None"
                # Ищем во всем файле CCV, подходящий по концентрации к этой пробе
                matches = []
                for pot in blocks:
                    if 'CCV' in str(pot['Type']):
                        target = extract_target(pot['Type'])
                        meas = pd.to_numeric(pot['avg'][el], errors='coerce')
                        if target and (0.8 * val_raw <= target <= 1.2 * val_raw):
                            matches.append({'f': target/meas, 'dist': abs(pot['idx'] - b['idx']), 'target': target})
                
                if matches:
                    # Берем ближайший по времени из тех, что подошли по концентрации
                    best = min(matches, key=lambda x: x['dist'])
                    f_drift, ccv_used = best['f'], f"CCV_{best['target']}"

                df_val = get_dilution(b['Label'])
                final_res = val_raw * f_drift * df_val
                
                t2_row[el] = f"{final_res:.4f}"
                t3_row[el] = f"{val_raw:.3f} * {f_drift:.3f} ({ccv_used}) * {df_val}"
            
            t2_data.append(t2_row)
            t3_data.append(t3_row)

    return pd.DataFrame(t1_data), pd.DataFrame(t2_data), pd.DataFrame(t3_data)

# --- ВЫПОЛНЕНИЕ ---
if uploaded_file and process_btn:
    raw_df = pd.read_csv(uploaded_file)
    t1, t2, t3 = run_analytical_engine(raw_df)
    
    # Сохранение в сессию для возможности скачивания
    st.session_state['t1'], st.session_state['t2'], st.session_state['t3'] = t1, t2, t3

# --- ОТОБРАЖЕНИЕ РЕЗУЛЬТАТОВ ---
if 't1' in st.session_state:
    # Кнопка скачивания появляется после расчета
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        st.session_state['t1'].to_excel(writer, index=False, sheet_name='1_Thresholds')
        st.session_state['t2'].to_excel(writer, index=False, sheet_name='2_Final_Results')
        st.session_state['t3'].to_excel(writer, index=False, sheet_name='3_Math_Log')
    
    st.download_button(
        label="📥 Download Full Excel Report",
        data=output.getvalue(),
        file_name="ICP_Analysis_Report.xlsx",
        mime="application/vnd.ms-excel"
    )

    tab1, tab2, tab3 = st.tabs(["📊 1. Thresholds & RSD", "✅ 2. Final Results", "📝 3. Math Log"])
    with tab1:
        st.dataframe(st.session_state['t1'], use_container_width=True)
    with tab2:
        st.dataframe(st.session_state['t2'], use_container_width=True)
    with tab3:
        st.dataframe(st.session_state['t3'], use_container_width=True)
