import pandas as pd
import numpy as np
import streamlit as st
import re
from io import BytesIO

# --- 1. ИНТЕРФЕЙС (СТРОГО ПО СКРИНШОТУ) ---
st.set_page_config(layout="wide", page_title="Rosen ICP Processor")
st.title("🔬 ICP-OES Data Processor")

c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    uploaded_file = st.file_uploader("Upload ICP CSV", type="csv")
with c2:
    process_btn = st.button("🚀 Start Processing", use_container_width=True)
with c3:
    st.write("**System Status:**")
    st.info("Ready" if uploaded_file else "Waiting...")

st.markdown("---")

# --- 2. ЯДРО (ПО ГАЙДУ И АГРИМЕНТУ) ---

def extract_val(type_str):
    """Агримент: берем число после последнего подчеркивания"""
    if pd.isna(type_str): return None
    parts = str(type_str).split('_')
    try: return float(parts[-1])
    except: return None

def get_df(label):
    """Разбавление из Label"""
    label = str(label)
    if '/' in label:
        try: return float(label.split('/')[-1])
        except: pass
    return 1.0

def run_processor(df):
    df.columns = [c.strip() for c in df.columns]
    elements = [c for c in df.columns if c not in ['Category', 'Label', 'Type']]
    
    # Парсим блоки (по 4 строки на образец)
    blocks = []
    for i in range(0, len(df), 4):
        sub = df.iloc[i:i+4]
        if len(sub) < 4: continue
        avg = sub[sub['Category'].str.contains('average', case=False)].iloc[0]
        sd = sub[sub['Category'].str.contains('SD', case=False)].iloc[0]
        rsd = sub[sub['Category'].str.contains('RSD', case=False)].iloc[0]
        blocks.append({'idx': i, 'Label': avg['Label'], 'Type': avg['Type'], 'avg': avg, 'sd': sd, 'rsd': rsd})

    t1_list, t2_list, t3_list = [], [], []

    # Предварительно собираем ВСЕ CCV для ускорения поиска
    all_ccvs = [b for b in blocks if 'CCV' in str(b['Type'])]

    for b in blocks:
        # T1: Thresholds & RSD
        t1_r = {'Label': b['Label'], 'Type': b['Type']}
        for el in elements:
            val = pd.to_numeric(b['avg'][el], errors='coerce')
            mql = pd.to_numeric(b['sd'][el], errors='coerce') * 10
            rsd_val = pd.to_numeric(b['rsd'][el], errors='coerce')
            if pd.isna(val): t1_r[el] = "N/A"
            elif val < mql: t1_r[el] = f"<{mql:.3f}"
            else:
                f = "!!" if rsd_val > 10 else ("!" if rsd_val > 6 else "")
                t1_r[el] = f"{val:.4f}{f}"
        t1_list.append(t1_r)

        # T2 & T3: Только для Samples (S)
        if b['Type'] == 'S':
            t2_r, t3_r = {'Label': b['Label']}, {'Label': b['Label']}
            for el in elements:
                c_raw = pd.to_numeric(b['avg'][el], errors='coerce')
                
                # Поиск CCV в окне +/- 20%
                f_drift = 1.0
                used_ccv = "None"
                
                matches = []
                for ccv_b in all_ccvs:
                    target = extract_val(ccv_b['Type'])
                    measured = pd.to_numeric(ccv_b['avg'][el], errors='coerce')
                    
                    if target and measured and measured > 0:
                        # ПРОВЕРКА: Проба 0.128 подходит к стандарту 0.1? Да (0.08 - 0.15)
                        if (0.8 * c_raw) <= target <= (1.2 * c_raw):
                            matches.append({
                                'f': target / measured, 
                                'dist': abs(ccv_b['idx'] - b['idx']),
                                'name': f"CCV_{target}"
                            })
                
                if matches:
                    # Если нашли несколько (например, CCV в начале и в конце), берем ближайший
                    best = min(matches, key=lambda x: x['dist'])
                    f_drift = best['f']
                    used_ccv = best['name']

                dil = get_df(b['Label'])
                res = c_raw * f_drift * dil
                
                t2_r[el] = f"{res:.4f}"
                t3_r[el] = f"{c_raw:.4f} * {f_drift:.3f} ({used_ccv}) * {dil}"
            
            t2_list.append(t2_r)
            t3_list.append(t3_r)

    return pd.DataFrame(t1_list), pd.DataFrame(t2_list), pd.DataFrame(t3_list)

# --- 3. ВЫВОД ---
if uploaded_file and process_btn:
    raw = pd.read_csv(uploaded_file)
    t1, t2, t3 = run_processor(raw)
    st.session_state['out'] = (t1, t2, t3)

if 'out' in st.session_state:
    t1, t2, t3 = st.session_state['out']
    
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as wr:
        t1.to_excel(wr, sheet_name='Thresholds', index=False)
        t2.to_excel(wr, sheet_name='Final Results', index=False)
        t3.to_excel(wr, sheet_name='Math Log', index=False)
    
    st.download_button("📥 Download Excel Report", buf.getvalue(), "Report.xlsx", "application/vnd.ms-excel")

    tabs = st.tabs(["📊 Thresholds", "✅ Final Results", "📝 Math Log"])
    tabs[0].dataframe(t1, use_container_width=True)
    tabs[1].dataframe(t2, use_container_width=True)
    tabs[2].dataframe(t3, use_container_width=True)
