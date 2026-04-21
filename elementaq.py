import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ v3.2", layout="wide")

# --- БОКОВАЯ ПАНЕЛЬ (SIDEBAR) ---
st.sidebar.header("Global Control Settings")
# Инициализируем все 3 бегунка
rsd_limit = st.sidebar.slider("RSD Threshold (%)", 1.0, 25.0, 10.0)
drift_threshold = st.sidebar.slider("CCV Drift Limit (%)", 1.0, 20.0, 10.0)
blank_threshold = st.sidebar.slider("Blank Max Threshold (abs)", 0.0, 1.0, 0.050, step=0.005)

def clean_val(text):
    if pd.isna(text) or text == "": return 0.0
    s = str(text).replace('<', '').replace('!', '').replace('*', '').strip()
    res = re.sub(r'[^0-9.eE-]', '', s)
    try: return float(res) if res else 0.0
    except: return 0.0

def extract_meta(label):
    lb = str(label)
    t_match = re.search(r'[_ ](\d+\.?\d*)$', lb)
    d_match = re.search(r'_dil(\d+\.?\d*)', lb)
    return (float(t_match.group(1)) if t_match else None), (float(d_match.group(1)) if d_match else 1.0)

# --- ОСНОВНОЙ ИНТЕРФЕЙС ---
st.title("🧪 ElementaQ v3.2")
uploaded_file = st.file_uploader("Upload Qtegra CSV", type="csv")

if uploaded_file:
    raw_data = pd.read_csv(uploaded_file)
    raw_data.columns = [c.strip() for c in raw_data.columns]
    elements = [c for c in raw_data.columns if c not in ['Category', 'Label', 'Type']]

    # Шаг 1: Индексация
    if st.button("📊 Step 1: Process & Index"):
        rows = []
        for i in range(0, len(raw_data) - (len(raw_data)%4), 4):
            block = raw_data.iloc[i:i+4]
            lbl = str(block['Label'].iloc[0]).strip()
            tp = str(block['Type'].iloc[0]).strip().upper()
            target, dil = extract_meta(lbl)
            entry = {'Index': (i//4)+1, 'Label': lbl, 'Type': tp, 'Target': target, 'Dilution': dil}
            for el in elements:
                v_raw = str(block[block['Category'].str.contains('average', case=False)][el].values[0])
                rsd = clean_val(block[block['Category'].str.contains('RSD', case=False)][el].values[0])
                v = clean_val(v_raw)
                txt = f"<{v:.4e}" if '<' in v_raw else f"{v:.9f}"
                if '<' not in v_raw and rsd > rsd_limit: txt += "!!"
                entry[el] = txt
            rows.append(entry)
        st.session_state.processed_df = pd.DataFrame(rows)

    if 'processed_df' in st.session_state and st.session_state.processed_df is not None:
        st.write("### Table 1: Input Data")
        st.dataframe(st.session_state.processed_df)

        # Шаг 2: Расчеты
        if st.button("🚀 Step 2: Run Metrological Correction"):
            df = st.session_state.processed_df.copy()
            res_list, aud_list = [], []

            for _, row in df.iterrows():
                idx = row['Index']
                res_r, aud_r = {'Index': idx, 'Label': row['Label']}, {'Index': idx, 'Label': row['Label']}
                
                for el in elements:
                    # Поиск CCV [cite: 87-92, 179-181]
                    ccv_pool = []
                    standards = df[df['Target'].notnull()]
                    for _, s in standards.iterrows():
                        m = clean_val(s[el])
                        if m > 0:
                            rec = (m / s['Target']) * 100
                            if (100 - drift_threshold) <= rec <= (100 + drift_threshold):
                                ccv_pool.append({'idx': s['Index'], 'f': s['Target']/m})
                    
                    if not ccv_pool:
                        res_r[el], aud_r[el] = "NO VALID CCV", "FAIL"
                        continue

                    # Интерполяция f [cite: 190, 217]
                    if len(ccv_pool) == 1: fi = ccv_pool[0]['f']
                    else:
                        idxs = [c['idx'] for c in ccv_pool]
                        if idx <= idxs[0]: fi = ccv_pool[0]['f']
                        elif idx >= idxs[-1]: fi = ccv_pool[-1]['f']
                        else:
                            for n in range(len(idxs)-1):
                                if idxs[n] <= idx <= idxs[n+1]:
                                    fs, fe = ccv_pool[n]['f'], ccv_pool[n+1]['f']
                                    fi = fs + (fe - fs) * (idx - idxs[n]) / (idxs[n+1] - idxs[n])
                                    break

                    # Master Equation [cite: 43-45, 205-207]
                    raw_v = clean_val(row[el])
                    c_drift = raw_v * fi if row['Type'] in ['S', 'BLK', 'MBB'] else raw_v
                    
                    blks = df[df['Type'].isin(['BLK', 'MBB'])]
                    # Бланки тоже нормализуем [cite: 173-175]
                    d_blks = [clean_val(b[el]) * ccv_pool[0]['f'] for _, b in blks.iterrows()]
                    avg_b = np.mean(d_blks) if d_blks else 0.0
                    
                    c_net = c_drift - (avg_b if row['Type'] == 'S' else 0.0)
                    final = c_net * row['Dilution']
                    
                    res_r[el] = f"{final:.4e}" if 0 < abs(final) < 1e-6 else f"{final:.9f}"
                    aud_r[el] = f"f:{fi:.3f}|B:{avg_b:.1e}"
                
                res_list.append(res_r); aud_list.append(aud_r)
            
            st.session_state.final_res = pd.DataFrame(res_list)
            st.session_state.final_aud = pd.DataFrame(aud_list)

        # ВЫВОД РЕЗУЛЬТАТОВ И КНОПКА СКАЧИВАНИЯ
        if 'final_res' in st.session_state:
            st.write("### Table 2: Final Results")
            st.dataframe(st.session_state.final_res)
            
            # Генерация CSV для скачивания
            csv = st.session_state.final_res.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Final Table (CSV)",
                data=csv,
                file_name="ElementaQ_Results.csv",
                mime="text/csv"
            )
