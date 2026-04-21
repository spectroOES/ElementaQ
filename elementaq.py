import streamlit as st
import pandas as pd
import numpy as np
import re
import io

# 1. СТАБИЛЬНАЯ КОНФИГУРАЦИЯ
st.set_page_config(page_title="ElementaQ v3.1", layout="wide")

# Инициализация слайдеров в боковой панели (SIDEBAR)
st.sidebar.header("Global Control Settings")
rsd_limit = st.sidebar.slider("RSD Threshold (%)", 1.0, 25.0, 10.0, key='rsd_slider')
drift_threshold = st.sidebar.slider("CCV Drift Limit (%)", 1.0, 20.0, 10.0, key='drift_slider')

# 2. ФУНКЦИИ ОЧИСТКИ И ПАРСИНГА
def clean_val(text):
    """Удаляет <, !, * и другие артефакты Qtegra"""
    if pd.isna(text) or text == "": return 0.0
    s = str(text).replace('<', '').replace('!', '').replace('*', '').strip()
    res = re.sub(r'[^0-9.eE-]', '', s)
    try: return float(res) if res else 0.0
    except: return 0.0

def extract_meta(label):
    """Извлекает концентрацию из меток типа 'MixI 0.01' или 'CCV_0.1'"""
    label_str = str(label)
    # Ищем любое число в конце строки после пробела или подчеркивания
    match = re.search(r'[_ ](\d+\.?\d*)$', label_str)
    target = float(match.group(1)) if match else None
    
    dil_match = re.search(r'_dil(\d+\.?\d*)', label_str)
    dilution = float(dil_match.group(1)) if dil_match else 1.0
    return target, dilution

# 3. ИНИЦИАЛИЗАЦИЯ ПАМЯТИ (SESSION STATE)
if 'processed_df' not in st.session_state: st.session_state.processed_df = None

st.title("🧪 ElementaQ v3.1: Final Fix")
uploaded_file = st.file_uploader("Upload CSV", type="csv")

if uploaded_file:
    raw_data = pd.read_csv(uploaded_file)
    raw_data.columns = [c.strip() for c in raw_data.columns]
    elements = [c for c in raw_data.columns if c not in ['Category', 'Label', 'Type']]

    # ШАГ 1: ИНДЕКСАЦИЯ И МЕТАДАННЫЕ
    if st.button("📊 Step 1: Process & Index"):
        rows = []
        for i in range(0, len(raw_data) - (len(raw_data)%4), 4):
            block = raw_data.iloc[i:i+4]
            lbl = str(block['Label'].iloc[0]).strip()
            # Важно: берем тип и сразу чистим
            tp = str(block['Type'].iloc[0]).strip().upper()
            target, dil = extract_meta(lbl)
            
            entry = {'Index': (i//4)+1, 'Label': lbl, 'Type': tp, 'Target': target, 'Dilution': dil}
            for el in elements:
                val_raw = str(block[block['Category'].str.contains('average', case=False, na=False)][el].values[0])
                rsd_raw = clean_val(block[block['Category'].str.contains('RSD', case=False, na=False)][el].values[0])
                v = clean_val(val_raw)
                # Форматирование для таблицы
                txt = f"<{v:.4e}" if '<' in val_raw else f"{v:.9f}"
                if '<' not in val_raw and rsd_raw > rsd_limit: txt += "!!"
                entry[el] = txt
            rows.append(entry)
        
        st.session_state.processed_df = pd.DataFrame(rows)

    # ОТОБРАЖЕНИЕ ТАБЛИЦЫ 1
    if st.session_state.processed_df is not None:
        st.write("### Table 1: Input Data (Ready for Calculation)")
        st.dataframe(st.session_state.processed_df)

        # ШАГ 2: РАСЧЕТ MASTER EQUATION
        if st.button("🚀 Step 2: Final Metrological Calculation"):
            df = st.session_state.processed_df.copy()
            res_list, aud_list = [], []

            for _, row in df.iterrows():
                idx = row['Index']
                res_row = {'Index': idx, 'Label': row['Label'], 'Type': row['Type']}
                aud_row = {'Index': idx, 'Label': row['Label'], 'Type': row['Type']}

                for el in elements:
                    # ПОИСК CCV (Исправлено: ищем 'CCV' или 'MIX' в типе или метке)
                    ccv_pool = []
                    # Фильтруем все строки, где есть целевая концентрация (Target)
                    valid_standards = df[df['Target'].notnull()]
                    
                    for _, s in valid_standards.iterrows():
                        meas = clean_val(s[el])
                        if meas > 0:
                            recovery = (meas / s['Target']) * 100
                            # Правило 10% дрейфа [cite: 216]
                            if (100 - drift_threshold) <= recovery <= (100 + drift_threshold):
                                ccv_pool.append({'idx': s['Index'], 'f': s['Target']/meas})
                    
                    if not ccv_pool:
                        res_row[el], aud_row[el] = "NO VALID CCV", "FAIL"
                        continue

                    # ГИБКИЙ РАСЧЕТ ФАКТОРА f [cite: 87-97]
                    if len(ccv_pool) == 1:
                        f_curr = ccv_pool[0]['f']
                    else:
                        idxs = [c['idx'] for c in ccv_pool]
                        if idx <= idxs[0]: f_curr = ccv_pool[0]['f']
                        elif idx >= idxs[-1]: f_curr = ccv_pool[-1]['f']
                        else:
                            # Линейная интерполяция [cite: 190]
                            for n in range(len(idxs)-1):
                                if idxs[n] <= idx <= idxs[n+1]:
                                    f_start, f_end = ccv_pool[n]['f'], ccv_pool[n+1]['f']
                                    f_curr = f_start + (f_end - f_start) * (idx - idxs[n]) / (idxs[n+1] - idxs[n])
                                    break

                    # MASTER EQUATION: Drift -> Blank -> Dilution [cite: 205-207]
                    raw_v = clean_val(row[el])
                    # 1. Коррекция дрейфа
                    c_drift = raw_v * f_curr if row['Type'] in ['S', 'BLK', 'MBB'] else raw_v
                    
                    # 2. Вычитание бланка (Бланки тоже корректируются по дрейфу первого стандарта)
                    all_blks = df[df['Type'].isin(['BLK', 'MBB'])]
                    drift_blks = [clean_val(b[el]) * ccv_pool[0]['f'] for _, b in all_blks.iterrows()]
                    avg_b = np.mean(drift_blks) if drift_blks else 0.0
                    
                    # Вычитаем бланк только из проб (S) [cite: 173]
                    c_net = c_drift - (avg_b if row['Type'] == 'S' else 0.0)
                    
                    # 3. Разбавление [cite: 168]
                    final = c_net * row['Dilution']
                    
                    res_row[el] = f"{final:.4e}" if 0 < abs(final) < 1e-6 else f"{final:.9f}"
                    aud_row[el] = f"f:{f_curr:.3f}|B:{avg_b:.1e}"

                res_list.append(res_row)
                aud_list.append(aud_row)
            
            st.write("### Table 2: Final Results (Corrected)"); st.dataframe(pd.DataFrame(res_list))
            st.write("### Table 3: Audit Log"); st.dataframe(pd.DataFrame(aud_list))
