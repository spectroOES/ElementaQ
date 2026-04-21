import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ v6.1", layout="wide")

# --- CORE PARSING ---
def clean_val(v):
    if pd.isna(v) or v == "n/a": return 0.0
    s = str(v).replace('!', '').replace('<', '').strip()
    try: return float(s)
    except: return 0.0

def get_logic(label, tp):
    lb, tp_str = str(label), str(tp).upper()
    target, dil = None, 1.0
    if "CCV" in tp_str or "ICV" in tp_str:
        t_match = re.search(r'_(\d+\.?\d*)', lb)
        if t_match: target = float(t_match.group(1))
    if tp_str == 'S':
        d_match = re.search(r'_dil(\d+\.?\d*)$', lb)
        if d_match: dil = float(d_match.group(1))
    return target, dil

st.title("🧪 ElementaQ v6.1 (Anticrash)")
file = st.file_uploader("Загрузите CSV", type="csv")

if file:
    # Пытаемся прочитать с нормализацией кодировки и очисткой имен колонок
    try:
        df_raw = pd.read_csv(file, encoding='utf-8-sig')
    except:
        df_raw = pd.read_csv(file, encoding='cp1251')
    
    # Очистка заголовков от мусора и скрытых пробелов
    df_raw.columns = [str(c).strip().replace('\ufeff', '') for c in df_raw.columns]
    
    # Проверка наличия ключевых колонок
    required = ['Label', 'Type']
    missing = [c for c in required if c not in df_raw.columns]
    
    if missing:
        st.error(f"В файле не найдены колонки: {missing}. Проверьте заголовки CSV.")
    else:
        elements = [c for c in df_raw.columns if c not in ['Category', 'Label', 'Type']]
        inc_t = st.sidebar.slider("Inclusion Threshold (%)", 10, 100, 50)

        # --- STEP 1 ---
        if st.button("📊 Шаг 1: Первичная обработка"):
            p1_rows = []
            for i in range(0, len(df_raw)-(len(df_raw)%4), 4):
                block = df_raw.iloc[i : i+4]
                name, tp = str(block['Label'].iloc[0]).strip(), str(block['Type'].iloc[0]).strip()
                tgt, dil = get_logic(name, tp)
                r = {'Index': (i//4)+1, 'Label': name, 'Type': tp, '_tgt': tgt, '_dil': dil}
                for el in elements:
                    try:
                        avg = block[el].iloc[0]
                        sd, rsd, mql = float(block[el].iloc[1]), float(block[el].iloc[2]), float(block[el].iloc[3])
                        r[el] = f"<{abs(sd * 10)}" if ("<LQ" in str(avg) or float(avg) < mql) else f"{float(avg)}{'!!' if rsd > 10 else ('!' if rsd > 6 else '')}"
                    except: r[el] = "n/a"
                p1_rows.append(r)
            st.session_state.p1_df = pd.DataFrame(p1_rows)

        if 'p1_df' in st.session_state:
            st.subheader("Таблица 1: Проверка Target и Разбавления")
            # Безопасный вывод: только те колонки, что точно созданы в Step 1
            st.dataframe(st.session_state.p1_df)

            # --- STEP 2 ---
            if st.button("🚀 Шаг 2: Расчет дрейфа и бланка"):
                p1 = st.session_state.p1_df.copy()
                res2, res3 = [], []
                blks = p1[p1['Type'] == 'BLK']
                avg_b = {el: np.mean([clean_val(v) for v in blks[el]]) if not blks.empty else 0.0 for el in elements}

                for idx, row in p1.iterrows():
                    r2, r3 = {'Index': row['Index'], 'Label': row['Label'], 'Type': row['Type']}, {'Index': row['Index'], 'Label': row['Label']}
                    for el in elements:
                        ccvs = []
                        for _, c in p1.iterrows():
                            if "CCV" in str(c['Type']).upper() and c['_tgt'] is not None:
                                m = clean_val(c[el])
                                if m >= (c['_tgt'] * inc_t / 100): ccvs.append({'idx': c['Index'], 'f': c['_tgt'] / m})
                        
                        f = 1.0
                        if ccvs:
                            bef = [v for v in ccvs if v['idx'] <= row['Index']]
                            aft = [v for v in ccvs if v['idx'] > row['Index']]
                            if bef and aft: f = bef[-1]['f'] + (aft[0]['f'] - bef[-1]['f']) * (row['Index'] - bef[-1]['idx']) / (aft[0]['idx'] - bef[-1]['idx'])
                            elif bef: f = bef[-1]['f']
                            elif aft: f = aft[0]['f']

                        raw = clean_val(row[el])
                        is_loq = "<" in str(row[el])
                        val_fin = raw * row['_dil'] if is_loq else max(0.0, (raw * f - (avg_b[el] if row['Type']=='S' else 0.0)) * row['_dil'])
                        r2[el] = f"{'<' if is_loq else ''}{val_fin:.8f}".rstrip('0').rstrip('.')
                        r3[el] = f"f:{f:.4f} B:{avg_b[el]:.1e}"
                    res2.append(r2); res3.append(r3)
                st.session_state.p2_df, st.session_state.p3_df = pd.DataFrame(res2), pd.DataFrame(res3)

            if 'p2_df' in st.session_state:
                st.subheader("Таблица 2: Итоговый протокол")
                st.dataframe(st.session_state.p2_df)
                buf = io.StringIO()
                buf.write("FINAL RESULTS\n"); st.session_state.p2_df.to_csv(buf, index=False)
                buf.write("\nAUDIT TRAIL\n"); st.session_state.p3_df.to_csv(buf, index=False)
                st.download_button("📥 Скачать отчет", buf.getvalue().encode('utf-8-sig'), "ElementaQ_Report.csv", "text/csv")
