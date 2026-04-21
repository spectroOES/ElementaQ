import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ v3.6", layout="wide")

# --- 1. ПАНЕЛЬ УПРАВЛЕНИЯ (SIDEBAR) ---
st.sidebar.header("Global Control Settings")
rsd_limit = st.sidebar.slider("RSD Threshold (%)", 1.0, 25.0, 10.0)
drift_limit = st.sidebar.slider("CCV Drift Limit (%)", 1.0, 20.0, 10.0)
blank_max = st.sidebar.slider("Blank Max Threshold (abs)", 0.0, 1.0, 0.050, step=0.005)
# Тот самый лимит в 20%, о котором мы говорили
blank_tolerance = st.sidebar.slider("Blank Tolerance (%)", 5.0, 50.0, 20.0)

def clean_num(text):
    if pd.isna(text) or text == "": return 0.0
    s = str(text).replace('<', '').replace('!', '').replace('*', '').strip()
    res = re.sub(r'[^0-9.eE-]', '', s)
    try: return float(res) if res else 0.0
    except: return 0.0

def get_metadata(label):
    lb = str(label)
    t_match = re.search(r'[_ ](\d+\.?\d*)$', lb)
    d_match = re.search(r'_dil(\d+\.?\d*)', lb)
    return (float(t_match.group(1)) if t_match else None), (float(d_match.group(1)) if d_match else 1.0)

# --- 2. ИНТЕРФЕЙС ---
st.title("🧪 ElementaQ v3.6")
st.caption("PhD Metrology Engine | 20% Blank Tolerance Logic")

if 'step1_df' not in st.session_state: st.session_state.step1_df = None
if 'res_df' not in st.session_state: st.session_state.res_df = None

uploaded_file = st.file_uploader("Upload Qtegra CSV", type="csv")

if uploaded_file:
    raw = pd.read_csv(uploaded_file)
    raw.columns = [c.strip() for c in raw.columns]
    elements = [c for c in raw.columns if c not in ['Category', 'Label', 'Type']]

    if st.button("📊 Step 1: Process & Index"):
        rows = []
        for i in range(0, len(raw) - (len(raw)%4), 4):
            block = raw.iloc[i:i+4]
            lbl = str(block['Label'].iloc[0]).strip()
            tp = str(block['Type'].iloc[0]).strip().upper()
            target, dil = get_metadata(lbl)
            entry = {'Index': (i//4)+1, 'Label': lbl, 'Type': tp, 'Target': target, 'Dilution': dil}
            for el in elements:
                v_raw = str(block[block['Category'].str.contains('average', case=False)][el].values[0])
                rsd_v = clean_num(block[block['Category'].str.contains('RSD', case=False)][el].values[0])
                v = clean_num(v_raw)
                txt = f"<{v:.4e}" if '<' in v_raw else f"{v:.9f}"
                if '<' not in v_raw and rsd_v > rsd_limit: txt += "!!"
                entry[el] = txt
            rows.append(entry)
        st.session_state.step1_df = pd.DataFrame(rows)

    if st.session_state.step1_df is not None:
        st.subheader("1. Indexed Input Data")
        st.dataframe(st.session_state.step1_df)

        if st.button("🚀 Step 2: Run Metrological Correction"):
            df = st.session_state.step1_df.copy()
            res_list, aud_list = [], []

            for _, row in df.iterrows():
                idx = row['Index']
                r_row, a_row = {'Index': idx, 'Label': row['Label'], 'Type': row['Type']}, {'Index': idx, 'Label': row['Label']}
                
                for el in elements:
                    # Поиск CCV
                    ccv_pool = []
                    stds = df[df['Target'].notnull()]
                    for _, s in stds.iterrows():
                        m = clean_num(s[el])
                        if m > 0:
                            rec = (m / s['Target']) * 100
                            if (100 - drift_limit) <= rec <= (100 + drift_limit):
                                ccv_pool.append({'idx': s['Index'], 'f': s['Target']/m})
                    
                    if not ccv_pool:
                        r_row[el], a_row[el] = "NO VALID CCV", "FAIL"
                        continue

                    # Интерполяция f
                    idxs = [c['idx'] for c in ccv_pool]
                    if len(ccv_pool) == 1 or idx <= idxs[0]: fi = ccv_pool[0]['f']
                    elif idx >= idxs[-1]: fi = ccv_pool[-1]['f']
                    else:
                        for n in range(len(idxs)-1):
                            if idxs[n] <= idx <= idxs[n+1]:
                                fs, fe = ccv_pool[n]['f'], ccv_pool[n+1]['f']
                                fi = fs + (fe - fs) * (idx - idxs[n]) / (idxs[n+1] - idxs[n])
                                break

                    # Master Equation
                    raw_v = clean_num(row[el])
                    c_drift = raw_v * fi if row['Type'] in ['S', 'BLK', 'MBB'] else raw_v
                    
                    # Логика Бланков
                    all_blks = df[df['Type'].isin(['BLK', 'MBB'])]
                    drift_blks = [clean_num(b[el]) * ccv_pool[0]['f'] for _, b in all_blks.iterrows()]
                    avg_b = np.mean(drift_blks) if drift_blks else 0.0
                    
                    # --- КРИТЕРИЙ ВАЛИДАЦИИ 20% ---
                    warnings = []
                    # Если значение бланка отклоняется от нормы более чем на заданный процент
                    if avg_b > blank_max * (1 + blank_tolerance/100):
                        warnings.append("Blank Error >20%")
                    
                    c_net = c_drift - (avg_b if row['Type'] == 'S' else 0.0)
                    final = c_net * row['Dilution']
                    
                    r_row[el] = f"{final:.4e}" if 0 < abs(final) < 1e-6 else f"{final:.9f}"
                    a_row[el] = f"f:{fi:.3f}|B:{avg_b:.1e}" + (f" ({', '.join(warnings)})" if warnings else "")
                
                res_list.append(r_row); aud_list.append(a_row)
            
            st.session_state.res_df = pd.DataFrame(res_list)
            st.session_state.aud_df = pd.DataFrame(aud_list)

        if st.session_state.res_df is not None:
            st.subheader("2. Final Corrected Results")
            st.dataframe(st.session_state.res_df)
            
            st.subheader("3. Metrological Audit Log")
            st.dataframe(st.session_state.aud_df)

            # --- ЭКСПОРТ ---
            report = io.StringIO()
            report.write("ELEMENTAQ V3.6 FULL REPORT\n" + "="*26 + "\n\n")
            report.write(f"SETTINGS: RSD<{rsd_limit}%, Drift<{drift_limit}%, BlankMax={blank_max}, Tolerance={blank_tolerance}%\n\n")
            report.write("--- SECTION 1: INPUT ---\n")
            st.session_state.step1_df.to_csv(report, index=False)
            report.write("\n--- SECTION 2: RESULTS ---\n")
            st.session_state.res_df.to_csv(report, index=False)
            report.write("\n--- SECTION 3: AUDIT ---\n")
            st.session_state.aud_df.to_csv(report, index=False)
            
            st.download_button("📥 Download Report (v3.6)", report.getvalue(), "ElementaQ_Report_v36.csv", "text/csv")
