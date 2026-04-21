import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ v4.7", layout="wide")

# --- Вспомогательные функции ---
def clean_num(val):
    if pd.isna(val) or val == "n/a": return 0.0
    s = str(val).replace('!', '').replace('<', '').strip()
    try: return float(s)
    except: return 0.0

def get_meta(label, row_type):
    lb, tp = str(label), str(row_type).upper()
    target, dilution = None, 1.0
    d_m = re.search(r'_dil(\d+\.?\d*)$', lb)
    if d_m: dilution = float(d_m.group(1))
    if tp in ['ICV', 'CCV']:
        t_m = re.search(r'_(\d+\.?\d*)(?:_dil\d+)?$', lb)
        if t_m: target = float(t_m.group(1))
    return target, dilution

# --- Sidebar ---
st.sidebar.header("Настройки RSD")
rsd_low = st.sidebar.slider("Yellow Flag (!)", 1.0, 15.0, 6.0, 0.5)
rsd_high = st.sidebar.slider("Red Flag (!!)", 1.0, 25.0, 10.0, 0.5)

st.sidebar.header("Настройки Метрологии")
inc_t = st.sidebar.slider("Inclusion Threshold (%)", 10, 100, 50)
match_w = st.sidebar.slider("Match Window (%)", 5.0, 500.0, (20.0, 200.0))

st.title("🧪 ElementaQ v4.7")
uploaded_file = st.file_uploader("Загрузите CSV файл", type="csv")

if uploaded_file:
    df = pd.read_csv(uploaded_file)
    df.columns = df.columns.str.strip()
    elements = [col for col in df.columns if col not in ['Category', 'Label', 'Type']]

    # --- ФАЗА 1: ВАШ ОРИГИНАЛЬНЫЙ КОД ---
    if st.button("📊 Запустить Фазу 1 (Фильтрация)"):
        final_results = []
        valid_rows = len(df) - (len(df) % 4)

        for i in range(0, valid_rows, 4):
            block = df.iloc[i : i + 4].copy()
            block['Category'] = block['Category'].astype(str).str.strip()
            sample_name = str(block['Label'].iloc[0]).strip()
            row_type = str(block['Type'].iloc[0]).upper() if 'Type' in block.columns else "S"
            
            target, dil = get_meta(sample_name, row_type)
            new_row = {'Index': (i//4)+1, 'Label': sample_name, 'Type': row_type, '_t': target, 'Dilution': dil}
            
            for el in elements:
                try:
                    # Извлечение данных (Ваша логика)
                    avg_val = block[block['Category'].str.contains('average', case=False, na=False)][el].values[0]
                    sd_val  = float(block[block['Category'].str.contains('SD', case=False, na=False)][el].values[0])
                    rsd_val = float(block[block['Category'].str.contains('RSD', case=False, na=False)][el].values[0])
                    mql_val = float(block[block['Category'].str.contains('MQL', case=False, na=False)][el].values[0])
                    
                    if "<LQ" in str(avg_val) or float(avg_val) < mql_val:
                        res = f"<{round(abs(sd_val * 10), 4)}" # Фикс минуса
                    else:
                        num_avg = float(avg_val)
                        if rsd_val > rsd_high: res = f"{num_avg}!!"
                        elif rsd_val > rsd_low: res = f"{num_avg}!"
                        else: res = str(num_avg)
                    new_row[el] = res
                except:
                    new_row[el] = "n/a"
            final_results.append(new_row)
        st.session_state.p1 = pd.DataFrame(final_results)

    if 'p1' in st.session_state:
        st.subheader("Таблица 1: Первичная фильтрация")
        st.dataframe(st.session_state.p1.drop(columns=['_t']))

        # --- ФАЗА 2: РАСЧЕТЫ ---
        if st.button("🚀 Запустить Фазу 2 (Метрология)"):
            p1 = st.session_state.p1.copy()
            p2_res, p3_res = [], []
            
            blks = p1[p1['Type'] == 'BLK']
            avg_b = {el: np.mean([clean_num(v) for v in blks[el]]) if not blks.empty else 0.0 for el in elements}

            for _, row in p1.iterrows():
                r2, r3 = {'Index': row['Index'], 'Label': row['Label'], 'Type': row['Type']}, {'Index': row['Index'], 'Label': row['Label']}
                for el in elements:
                    raw_v = clean_num(row[el])
                    f = 1.0
                    
                    ccvs = p1[(p1['Type'] == 'CCV') & (p1['_t'].notnull())]
                    if not ccvs.empty:
                        pts = [c for _, c in ccvs.iterrows() if clean_num(c[el]) >= (c['_t'] * inc_t / 100)]
                        if pts:
                            best = min(pts, key=lambda x: abs(x['Index'] - row['Index']))
                            f = best['_t'] / clean_num(best[el])
                    
                    v_drift = raw_v * f
                    v_net = v_drift - (avg_b[el] if row['Type'] == 'S' else 0.0)
                    is_loq = "<" in str(row[el])
                    
                    # Финальное значение: без минусов
                    final_v = max(0.0, raw_v * row['Dilution']) if is_loq else max(0.0, v_net * row['Dilution'])
                    
                    r2[el] = f"{'<' if is_loq else ''}{final_v:.4f}"
                    r3[el] = f"f:{f:.2f} B:{avg_b[el]:.2e}"
                p2_res.append(r2); p3_res.append(r3)
            
            st.session_state.p2 = pd.DataFrame(p2_res)
            st.session_state.p3 = pd.DataFrame(p3_res)

        if 'p2' in st.session_state:
            st.subheader("Таблица 2: Итоговые результаты (Протокол)")
            st.dataframe(st.session_state.p2)
            st.subheader("Таблица 3: Аудит (Дрейф и Бланки)")
            st.dataframe(st.session_state.p3)
            
            csv = st.session_state.p2.to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 Скачать Отчет (Таблица 2)", csv, "ElementaQ_Report.csv", "text/csv")
