import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ v6.0", layout="wide")

# --- CORE PARSING ---
def clean_val(v):
    if pd.isna(v) or v == "n/a": return 0.0
    s = str(v).replace('!', '').replace('<', '').strip()
    try: return float(s)
    except: return 0.0

def get_logic(label, tp):
    lb, tp_str = str(label), str(tp).upper()
    target, dil = None, 1.0
    # ТАРГЕТ: Теперь ищем подстроку "CCV" или "ICV" в типе (поймает CCV_0.01)
    if "CCV" in tp_str or "ICV" in tp_str:
        t_match = re.search(r'_(\d+\.?\d*)', lb)
        if t_match: target = float(t_match.group(1))
    # РАЗБАВЛЕНИЕ: Только для образцов типа S
    if tp_str == 'S':
        d_match = re.search(r'_dil(\d+\.?\d*)$', lb)
        if d_match: dil = float(d_match.group(1))
    return target, dil

st.title("🧪 ElementaQ v6.0 (Production Build)")
file = st.file_uploader("Загрузите CSV файл Qtegra", type="csv")

if file:
    df_raw = pd.read_csv(file)
    df_raw.columns = df_raw.columns.str.strip()
    elements = [c for c in df_raw.columns if c not in ['Category', 'Label', 'Type']]
    
    # Конфигурация интерфейса
    inc_t = st.sidebar.slider("Inclusion Threshold (%)", 10, 100, 50)
    r_l, r_h = st.sidebar.slider("RSD Flags (Yellow/Red)", 1.0, 20.0, (6.0, 10.0))

    # --- STEP 1: PARSING ---
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
                    if "<LQ" in str(avg) or float(avg) < mql:
                        r[el] = f"<{abs(sd * 10)}" # Прецизионный LOQ
                    else:
                        f = "!!" if rsd > r_h else ("!" if rsd > r_l else "")
                        r[el] = f"{float(avg)}{f}"
                except: r[el] = "n/a"
            p1_rows.append(r)
        st.session_state.p1_df = pd.DataFrame(p1_rows)

    if 'p1_df' in st.session_state:
        st.subheader("Таблица 1: Проверка Target и Разбавления")
        # Показываем служебные колонки для контроля
        st.dataframe(st.session_state.p1_df[['Index', 'Label', 'Type', '_tgt', '_dil']])

        # --- STEP 2: METROLOGY ---
        if st.button("🚀 Шаг 2: Расчет дрейфа и бланка"):
            p1 = st.session_state.p1_df.copy()
            res2, res3 = [], []
            avg_b = {el: np.mean([clean_val(v) for v in p1[p1['Type']=='BLK'][el]]) if not p1[p1['Type']=='BLK'].empty else 0.0 for el in elements}

            for idx, row in p1.iterrows():
                r2, r3 = {'Index': row['Index'], 'Label': row['Label'], 'Type': row['Type']}, {'Index': row['Index'], 'Label': row['Label']}
                for el in elements:
                    # Поиск всех валидных CCV для текущего элемента
                    ccvs = []
                    for _, c in p1.iterrows():
                        if "CCV" in str(c['Type']).upper() and c['_tgt'] is not None:
                            meas = clean_val(c[el])
                            if meas >= (c['_tgt'] * inc_t / 100):
                                ccvs.append({'idx': c['Index'], 'f': c['_tgt'] / meas})
                    
                    f = 1.0
                    if ccvs:
                        bef = [v for v in ccvs if v['idx'] <= row['Index']]
                        aft = [v for v in ccvs if v['idx'] > row['Index']]
                        if bef and aft: # Линейная интерполяция
                            c1, c2 = bef[-1], aft[0]
                            f = c1['f'] + (c2['f'] - c1['f']) * (row['Index'] - c1['idx']) / (c2['idx'] - c1['idx'])
                        elif bef: f = bef[-1]['f']
                        elif aft: f = aft[0]['f']

                    raw = clean_val(row[el])
                    is_loq = "<" in str(row[el])
                    # Финальный расчет: (Raw * f - Blank) * Dilution
                    if is_loq:
                        val_fin = raw * row['_dil']
                    else:
                        val_fin = max(0.0, (raw * f - (avg_b[el] if row['Type']=='S' else 0.0)) * row['_dil'])
                    
                    r2[el] = f"{'<' if is_loq else ''}{val_fin:.8f}".rstrip('0').rstrip('.')
                    r3[el] = f"f:{f:.4f} B:{avg_b[el]:.1e}"
                res2.append(r2); res3.append(r3)
            st.session_state.p2_df, st.session_state.p3_df = pd.DataFrame(res2), pd.DataFrame(res3)

        if 'p2_df' in st.session_state:
            st.subheader("Таблица 2: Итоговый протокол")
            st.dataframe(st.session_state.p2_df)
            st.subheader("Таблица 3: Аудит (Drift Factors)")
            st.dataframe(st.session_state.p3_df)
            
            buf = io.StringIO()
            buf.write("FINAL RESULTS\n"); st.session_state.p2_df.to_csv(buf, index=False)
            buf.write("\nAUDIT TRAIL\n"); st.session_state.p3_df.to_csv(buf, index=False)
            st.download_button("📥 Скачать отчет", buf.getvalue().encode('utf-8-sig'), "ElementaQ_v6.csv", "text/csv")
