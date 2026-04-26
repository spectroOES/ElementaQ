import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO

# --- 1. CONFIG & UI ---
st.set_page_config(layout="wide", page_title="ElementaQ")
st.title("🔬 ElementaQ: ICP-OES Analytical Engine v13.12")

def reset_all():
    st.session_state.results = None

if 'results' not in st.session_state:
    st.session_state.results = None

with st.sidebar:
    st.header("⚙️ QC Settings")
    rsd_l = st.sidebar.slider("Yellow Flag RSD %", 1.0, 15.0, 6.0)
    rsd_h = st.sidebar.slider("Red Flag RSD %", 1.0, 25.0, 10.0)
    st.markdown("---")
    st.header("📈 Drift Calibration (Gaid v2.0)")
    fit_window = st.number_input("CCV Match Window (+/- %)", 5.0, 100.0, 20.0)
    d_deadband = st.number_input("Tier A: No Correction Zone %", 0.0, 10.0, 5.0)
    d_max = st.number_input("Tier C: Failure Limit %", 5.0, 50.0, 10.0)

# --- 2. CORE FUNCTIONS ---

def to_num(val):
    if pd.isna(val) or val == "": return None
    try:
        s = re.sub(r'[!<>]', '', str(val)).strip()
        return float(s)
    except: return None

def get_target(type_str):
    match = re.search(r'_([\d.]+)$', str(type_str))
    return float(match.group(1)) if match else None

def calculate_f_drift(measured, target, deadband, failure_limit):
    if measured is None or target is None or target == 0: return 1.0, "None"
    diff = abs((measured - target) / target) * 100
    if diff <= deadband: return 1.0, "Tier A"
    elif diff > failure_limit: return 1.0, "Tier C (FAIL)"
    else: return float(target / measured), "Tier B"

# --- 3. PROCESSING ENGINE ---

uploaded_file = st.file_uploader("Upload ICP CSV", type="csv", on_change=reset_all)

if uploaded_file and st.button("🚀 Execute Analysis"):
    try:
        df = pd.read_csv(uploaded_file)
        df.columns = df.columns.str.strip()
        elements = [c for c in df.columns if c not in ['Category', 'Label', 'Type']]
        
        blocks = []
        for i in range(0, len(df) - (len(df) % 4), 4):
            sub = df.iloc[i:i+4]
            try:
                avg = sub[sub['Category'].str.contains('average', case=False)].iloc[0]
                sd  = sub[sub['Category'].str.contains('SD', case=False)].iloc[0]
                rsd = sub[sub['Category'].str.contains('RSD', case=False)].iloc[0]
                mql = sub[sub['Category'].str.contains('MQL', case=False)].iloc[0]
                blocks.append({
                    'idx': i, 'Label': avg['Label'], 'Type': avg['Type'], 
                    'avg': avg, 'sd': sd, 'rsd': rsd, 'mql': mql, 
                    'f_drift': {}, 'drift_note': {}
                })
            except: continue

        # 1. DRIFT CALCULATION
        for el in elements:
            ccv_pool = []
            for b in blocks:
                if 'CCV' in str(b['Type']).upper():
                    t = get_target(b['Type'])
                    m = to_num(b['avg'][el])
                    if t is not None and m is not None:
                        f, note = calculate_f_drift(m, t, d_deadband, d_max)
                        ccv_pool.append({'idx': b['idx'], 'target': t, 'f': f, 'note': note})

            for b in blocks:
                raw = to_num(b['avg'][el])
                if raw is None:
                    b['f_drift'][el], b['drift_note'][el] = 1.0, "No Data"
                    continue

                # Filter: Concentration Match (+/- 20%) [cite: 32, 58, 94]
                match = [p for p in ccv_pool if (1-fit_window/100)*raw <= p['target'] <= (1+fit_window/100)*raw]
                
                if not match:
                    b['f_drift'][el], b['drift_note'][el] = 1.0, "No Fit"
                    continue

                before = [p for p in match if p['idx'] < b['idx']]
                after = [p for p in match if p['idx'] > b['idx']]

                if before and after:
                    p1, p2 = before[-1], after[0]
                    # Identical Aliquot Rule [cite: 39, 103]
                    if p1['target'] == p2['target']:
                        if "FAIL" in p1['note'] or "FAIL" in p2['note']:
                            b['f_drift'][el], b['drift_note'][el] = 1.0, "QC FAIL"
                        else:
                            # Linear Interpolation [cite: 41, 42, 100]
                            dist, pos = (p2['idx'] - p1['idx']), (b['idx'] - p1['idx'])
                            f_i = p1['f'] + (p2['f'] - p1['f']) * (pos / dist)
                            b['f_drift'][el], b['drift_note'][el] = f_i, f"Interp({p1['target']})"
                    else:
                        p_n = min([p1, p2], key=lambda x: abs(x['idx'] - b['idx']))
                        b['f_drift'][el], b['drift_note'][el] = p_n['f'], f"Single({p_n['target']})"
                elif before or after:
                    p_n = before[-1] if before else after[0]
                    b['f_drift'][el], b['drift_note'][el] = p_n['f'], f"Single({p_n['target']})"
                else:
                    b['f_drift'][el], b['drift_note'][el] = 1.0, "No Match"

        # 2. BLANK & FINAL CALC [cite: 18, 62, 109]
        blanks = {el: np.mean([to_num(b['avg'][el])*b['f_drift'].get(el,1.0) 
                  for b in blocks if any(x in str(b['Type']).upper() for x in ['BLK','MBB'])]) or 0.0 
                  for el in elements}

        t1_r, t2_r, t3_r = [], [], []
        mql_map = {el: [] for el in elements}

        for b in blocks:
            r1, is_loq = {'Label': b['Label'], 'Type': b['Type']}, {}
            for el in elements:
                v_raw, v_mql = to_num(b['avg'][el]), (to_num(b['mql'][el]) or 0.0)
                v_sd = to_num(b['sd'][el]) or 0.0
                loq = round(v_sd * 10, 4)
                mql_map[el].append(loq)

                if v_raw is None or v_raw < v_mql or "<" in str(b['avg'][el]):
                    r1[el], is_loq[el] = f"<{loq}", loq
                else:
                    rsd_v = to_num(b['rsd'][el]) or 0.0
                    flag = "!!" if rsd_v > rsd_h else ("!" if rsd_v > rsd_l else "")
                    r1[el], is_loq[el] = f"{v_raw}{flag}", None
            t1_r.append(r1)
            
            if str(b['Type']).startswith('S'):
                r2, r3 = {'Label': b['Label']}, {'Label': b['Label']}
                dil = get_target(b['Type']) or 1.0
                for el in elements:
                    if is_loq[el] is not None:
                        r2[el] = f"<{round(is_loq[el] * dil, 4)}"
                        r3[el] = f"LOQ {is_loq[el]} * DF {dil}"
                    else:
                        v, f, bl = to_num(b['avg'][el]), b['f_drift'][el], blanks[el]
                        res = (v * f - bl) * dil
                        r2[el] = round(res, 4)
                        r3[el] = f"({v:.3f}*f:{f:.2f}-bl:{bl:.3f})*DF:{dil}"
                t2_r.append(r2); t3_r.append(r3)

        # MQL Reference Footer
        mq_row = {'Label': '--- MQL REF (SD*10) ---', 'Type': 'REF'}
        for el in elements: mq_row[el] = round(np.mean(mql_map[el]), 4) if mql_map[el] else 0.0
        t1_r.append(mq_row)

        st.session_state.results = (pd.DataFrame(t1_r), pd.DataFrame(t2_r), pd.DataFrame(t3_r))
    except Exception as e:
        st.error(f"Execution Error: {e}")

# --- 4. OUTPUT ---
if st.session_state.results:
    t1, t2, t3 = st.session_state.results
    st.subheader("📊 1. Thresholds (with MQL Footer)"); st.dataframe(t1, use_container_width=True)
    st.subheader("✅ 2. Final Results"); st.dataframe(t2, use_container_width=True)
    st.subheader("📝 3. Math Audit Trail"); st.dataframe(t3, use_container_width=True)
