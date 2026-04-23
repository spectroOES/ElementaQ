import pandas as pd
import numpy as np
import streamlit as st
import re
from io import BytesIO

# --- 1. ИНТЕРФЕЙС (СТРОГО ПО СКРИНШОТУ И НАЧАЛУ ДИАЛОГА) ---
st.set_page_config(layout="wide", page_title="ICP Processor")
st.title("🔬 ICP-OES Data Processor")

# Три виджета сверху в ряд
c1, c2, c3 = st.columns(3)
with c1:
    uploaded_file = st.file_uploader("Upload ICP CSV", type="csv")
with c2:
    # Кнопка запуска расчета
    process_btn = st.button("🚀 Start Processing", use_container_width=True)
with c3:
    st.write("**System Status:**")
    if uploaded_file:
        st.success("File uploaded")
    else:
        st.info("Waiting for file...")

st.markdown("---")

# --- 2. ЛОГИКА (ПО ГАЙДУ И АГРИМЕНТУ) ---

def extract_target_conc(type_str):
    """Синтаксис по Агрименту: берем число после последнего подчеркивания"""
    if pd.isna(type_str): return None
    parts = str(type_str).split('_')
    if len(parts) > 1:
        try:
            return float(parts[-1])
        except ValueError:
            return None
    return None

def get_dilution_factor(label):
    """Поиск коэффициента разбавления в имени образца"""
    label = str(label)
    if '/' in label:
        try: return float(label.split('/')[-1])
        except: pass
    if '_dil' in label:
        try: return float(label.split('_dil')[-1])
        except: pass
    return 1.0

def process_analytical_data(df):
    df.columns = [c.strip() for c in df.columns]
    elements = [c for c in df.columns if c not in ['Category', 'Label', 'Type']]
    
    # Группировка строк по 4 (avg, sd, rsd, mql)
    rows_per_sample = 4
    blocks = []
    for i in range(0, len(df), rows_per_sample):
        subset = df.iloc[i:i+rows_per_sample]
        if len(subset) < 4: continue
        
        avg = subset[subset['Category'].str.contains('average', case=False)].iloc[0]
        sd = subset[subset['Category'].str.contains('SD', case=False)].iloc[0]
        rsd = subset[subset['Category'].str.contains('RSD', case=False)].iloc[0]
        
        blocks.append({
            'pos': i,
            'Label': avg['Label'],
            'Type': avg['Type'],
            'avg': avg,
            'sd': sd,
            'rsd': rsd
        })

    t1_rows, t2_rows, t3_rows = [], [], []

    for b in blocks:
        # Таблица 1: Пороги и RSD флаги
        t1_item = {'Label': b['Label'], 'Type': b['Type']}
        for el in elements:
            val = pd.to_numeric(b['avg'][el], errors='coerce')
            mql = pd.to_numeric(b['sd'][el], errors='coerce') * 10
            rsd_val = pd.to_numeric(b['rsd'][el], errors='coerce')
            
            if pd.isna(val): t1_item[el] = "N/A"
            elif val < mql: t1_item[el] = f"<{mql:.3f}"
            else:
                flag = "!!" if rsd_val > 10 else ("!" if rsd_val > 6 else "")
                t1_item[el] = f"{val:.4f}{flag}"
        t1_rows.append(t1_item)

        # Таблицы 2 и 3: Расчеты только для образцов (S)
        if b['Type'] == 'S':
            t2_item, t3_item = {'Label': b['Label']}, {'Label': b['Label']}
            for el in elements:
                c_raw = pd.to_numeric(b['avg'][el], errors='coerce')
                
                # ЖЕСТКИЙ ПОДБОР CCV (+/- 20%)
                f_drift = 1.0
                ccv_label = "No Match"
                
                suitable_ccvs = []
                for potential in blocks:
                    if 'CCV' in str(potential['Type']):
                        target = extract_target_conc(potential['Type'])
                        measured = pd.to_numeric(potential['avg'][el], errors='coerce')
                        
                        if target and measured > 0:
                            # Проверка попадания в окно 20%
                            if (0.8 * c_raw) <= target <= (1.2 * c_raw):
                                suitable_ccvs.append({
                                    'f': target / measured,
                                    'dist': abs(potential['pos'] - b['pos']),
                                    'target': target
                                })
                
                if suitable_ccvs:
                    # Берем ближайший по времени (по позиции в файле)
                    best_match = min(suitable_ccvs, key=lambda x: x['dist'])
                    f_drift = best_match['f']
                    ccv_label = f"CCV_{best_match['target']}"

                df_factor = get_dilution_factor(b['Label'])
                c_final = c_raw * f_drift * df_factor
                
                t2_item[el] = f"{c_final:.4f}"
                t3_item[el] = f"{c_raw:.3f} * {f_drift:.3f} ({ccv_label}) * {df_factor}"
            
            t2_rows.append(t2_item)
            t3_rows.append(t3_item)

    return pd.DataFrame(t1_rows), pd.DataFrame(t2_rows), pd.DataFrame(t3_rows)

# --- 3. ВЫВОД РЕЗУЛЬТАТОВ ---

if uploaded_file and process_btn:
    data = pd.read_csv(uploaded_file)
    t1, t2, t3 = process_analytical_data(data)
    
    # Сохраняем в session_state для скачивания
    st.session_state['results'] = (t1, t2, t3)

if 'results' in st.session_state:
    t1, t2, t3 = st.session_state['results']
    
    # Кнопка скачивания Excel
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        t1.to_excel(writer, sheet_name='Thresholds', index=False)
        t2.to_excel(writer, sheet_name='Final Results', index=False)
        t3.to_excel(writer, sheet_name='Math Log', index=False)
    
    st.download_button(
        label="📥 Download Excel Report",
        data=buffer.getvalue(),
        file_name="ICP_Processing_Report.xlsx",
        mime="application/vnd.ms-excel"
    )

    # Вкладки с таблицами
    tabs = st.tabs(["📊 1. Thresholds & RSD", "✅ 2. Final Results", "📝 3. Math Log"])
    with tabs[0]:
        st.dataframe(t1, use_container_width=True)
    with tabs[1]:
        st.dataframe(t2, use_container_width=True)
    with tabs[2]:
        st.dataframe(t3, use_container_width=True)
