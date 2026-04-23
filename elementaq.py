import pandas as pd
import numpy as np
import streamlit as st
import re

def extract_target(type_val):
    if pd.isna(type_val): return None
    match = re.search(r'_(\d+\.?\d*)$', str(type_val))
    return float(match.group(1)) if match else None

def process_icp_data(df):
    df.columns = [c.strip() for c in df.columns]
    element_cols = [c for c in df.columns if c not in ['Category', 'Label', 'Type']]
    
    blocks = []
    for i in range(0, len(df), 4):
        block = df.iloc[i:i+4]
        if len(block) < 4: continue
        try:
            avg = block[block['Category'].str.contains('average', case=False)].iloc[0]
            blocks.append({
                'idx': i, 
                'Label': avg['Label'], 
                'Type': avg['Type'], 
                'avg': avg[element_cols]
            })
        except: continue

    t2_results, t3_logs = [], []

    for b in blocks:
        if b['Type'] == 'S':
            res_row = {'Label': b['Label']}
            log_row = {'Label': b['Label']}
            
            for col in element_cols:
                val_raw = pd.to_numeric(b['avg'][col], errors='coerce')
                if pd.isna(val_raw):
                    res_row[col] = "N/A"
                    continue

                # --- ЖЕСТКИЙ ПОДБОР СТАНДАРТА (+/- 20%) ---
                suitable_ccvs = []
                for potential_ccv in blocks:
                    if 'CCV' in str(potential_ccv['Type']):
                        target = extract_target(potential_ccv['Type'])
                        measured = pd.to_numeric(potential_ccv['avg'][col], errors='coerce')
                        
                        if target and measured > 0:
                            # Проверка критерия: отклонение не более 20%
                            if (0.8 * val_raw) <= target <= (1.2 * val_raw):
                                suitable_ccvs.append({
                                    'dist': abs(potential_ccv['idx'] - b['idx']),
                                    'f': target / measured,
                                    't': target
                                })

                # Выбираем ближайший по времени из подходящих по концентрации
                if suitable_ccvs:
                    best_match = min(suitable_ccvs, key=lambda x: x['dist'])
                    f_drift = best_match['f']
                    ccv_label = f"CCV_{best_match['t']}"
                else:
                    f_drift = 1.0
                    ccv_label = "No match (1.0)"

                # Разбавление
                df_factor = 1.0
                label_str = str(b['Label'])
                if '/' in label_str:
                    try: df_factor = float(label_str.split('/')[-1])
                    except: pass
                elif '_dil' in label_str:
                    try: df_factor = float(label_str.split('_dil')[-1])
                    except: pass

                final_res = val_raw * f_drift * df_factor
                res_row[col] = f"{final_res:.4f}"
                log_row[col] = f"{val_raw:.3f} * {f_drift:.3f} ({ccv_label}) * {df_factor}"

            t2_results.append(res_row)
            t3_logs.append(log_row)

    return pd.DataFrame(t2_results), pd.DataFrame(t3_logs)

# --- UI (Layout по запросу) ---
st.set_page_config(layout="wide")
st.title("ICP-OES Result Processor")

# Три виджета сверху
c1, c2, c3 = st.columns(3)
with c1: st.write("**Criterion:** ±20% Concentration Match")
with c2: st.write("**Mode:** Time-Nearest Suitable CCV")
with c3: st.write("**Units:** mg/L")

st.markdown("---")
# Загрузка в центре
uploaded_file = st.file_uploader("Upload CSV File", type="csv")
st.markdown("---")

if uploaded_file:
    df_raw = pd.read_csv(uploaded_file)
    t_res, t_log = process_icp_data(df_raw)
    
    tab1, tab2 = st.tabs(["Final Results", "Detailed Calculation Log"])
    with tab1:
        st.dataframe(t_res, use_container_width=True)
    with tab2:
        st.dataframe(t_log, use_container_width=True)
