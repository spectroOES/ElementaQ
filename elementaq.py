import streamlit as st
import pandas as pd
import numpy as np
import re
import io

st.set_page_config(page_title="ElementaQ - Precision Suite", layout="wide", page_icon="🧪")

def parse_metadata(name):
    target_match = re.search(r'_(\d+\.?\d*)$', str(name))
    dilution_match = re.search(r'_dil(\d+\.?\d*)', str(name))
    target = float(target_match.group(1)) if target_match else None
    dilution = float(dilution_match.group(1)) if dilution_match else 1.0
    return target, dilution

def format_value(val, is_lq=False):
    if is_lq:
        prefix = "<"
        val = max(abs(val), 1e-12)
    else:
        prefix = ""
    if 0 < abs(val) < 1e-6:
        return f"{prefix}{val:.4e}"
    else:
        return f"{prefix}{val:.9f}"

def calculate_drift_factor(idx, ccv_map, target_val):
    indices = sorted(ccv_map.keys())
    if not indices: return 1.0
    if idx <= indices[0]: return target_val / ccv_map[indices[0]]
    if idx >= indices[-1]: return target_val / ccv_map[indices[-1]]
    for j in range(len(indices) - 1):
        idx_start, idx_end = indices[j], indices[j+1]
        if idx_start <= idx <= idx_end:
            v_start, v_end = ccv_map[idx_start], ccv_map[idx_end]
            interp = v_start + (v_end - v_start) * (idx - idx_start) / (idx_end - idx_start)
            return target_val / interp
    return 1.0

st.title("🧪 ElementaQ: Integrated Analytical Suite")
st.markdown("---")

# Sidebar Logic
st.sidebar.header("Phase 1: RSD Control")
rsd_low = st.sidebar.slider("Yellow Flag (!)", 1.0, 15.0, 6.0)
rsd_high = st.sidebar.slider("Red Flag (!!)", 1.0, 25.0, 10.0)

uploaded_file = st.file_uploader("Upload Qtegra CSV", type="csv")

if uploaded_file:
    raw_df = pd.read_csv(uploaded_file)
    raw_df.columns = raw_df.columns.str.strip()
    elements = [col for col in raw_df.columns if col not in ['Category', 'Label', 'Type']]
    
    # --- PHASE 1 ---
    st.write("## 🟢 STEP 01: Stability Analysis (RSD)")
    final_p1 = []
    total_rows = len(raw_df)
    valid_rows = total_rows - (total_rows % 4)

    for i in range(0, valid_rows, 4):
        block = raw_df.iloc[i : i + 4].copy()
        label = str(block['Label'].iloc[0]).strip()
        stype = str(block['Type'].iloc[0]).strip().upper()
        
        new_row = {'Label': label, 'Type': stype}
        for el in elements:
            try:
                avg_v = block[block['Category'].str.contains('average', case=False, na=False)][el].values[0]
                rsd_v = float(block[block['Category'].str.contains('RSD', case=False, na=False)][el].values[0])
                is_lq = '<LQ' in str(avg_v)
                clean_v = float(re.sub(r'[^0-9.eE-]', '', str(avg_v).split('<')[0]))
                
                res = format_value(clean_v, is_lq)
                if not is_lq:
                    if rsd_v > rsd_high: res += "!!"
                    elif rsd_v > rsd_low: res += "!"
                new_row[el] = res
            except: new_row[el] = "0.000000000"
        final_p1.append(new_row)
    
    ph1_df = pd.DataFrame(final_p1)
    st.dataframe(ph1_df)

    # --- PHASE 2 ---
    if st.button("🚀 Run Phase 2: Targeted Metrology"):
        st.write("## 🔵 STEP 02: Final Corrected Results")
        ph1_df['Target'], ph1_df['Dilution'] = zip(*ph1_df['Label'].map(parse_metadata))
        ph1_df['Row_Idx'] = range(len(ph1_df))
        ph2_df = ph1_df.copy()
        
        for el in elements:
            # 1. Фильтруем CCV: исключаем те, у которых есть "!!" (RSD > 10%)
            ccv_data = ph1_df[(ph1_df['Type'] == 'CCV') & (~ph1_df[el].astype(str).str.contains('!!'))]
            
            target_v = None
            ccv_map = {}
            if not ccv_data.empty:
                target_v = ccv_data['Target'].iloc[0]
                ccv_map = {idx: float(re.sub(r'[^0-9.eE-]', '', str(v).split('!')[0])) 
                           for idx, v in zip(ccv_data['Row_Idx'], ccv_data[el])}
            
            # 2. Средний бланк (Instrumental Blank - ONLY BLK)
            blanks = ph1_df[ph1_df['Type'] == 'BLK'][el].apply(
                lambda x: float(re.sub(r'[^0-9.eE-]', '', str(x).split('!')[0]))
            )
            avg_blank = blanks.mean() if not blanks.empty else 0.0
            
            # 3. Применяем расчеты
            for i, row in ph2_df.iterrows():
                val = float(re.sub(r'[^0-9.eE-]', '', str(row[el]).split('!')[0]))
                is_lq = '<' in str(row[el])
                stype = row['Type']
                
                # Коэффициент дрейфа считаем для всех, но...
                f_drift = calculate_drift_factor(i, ccv_map, target_v) if target_v else 1.0
                
                # ЛОГИКА ВЫЧИТАНИЯ:
                # Только для образцов (S). BLK, MBB и CCV остаются без вычитания фона.
                subtraction_val = avg_blank if stype == 'S' else 0.0
                
                corrected = (val * f_drift - subtraction_val) * row['Dilution']
                ph2_df.at[i, el] = format_value(corrected, is_lq)

        final_res = ph2_df.drop(columns=['Target', 'Dilution', 'Row_Idx'])
        st.dataframe(final_res)
        
        # Combined Download
        output = io.StringIO()
        output.write("STEP 01: RSD STABILITY REPORT\n")
        ph1_df.drop(columns=['Target', 'Dilution', 'Row_Idx']).to_csv(output, index=False)
        output.write("\n\nSTEP 02: FINAL METROLOGICAL REPORT\n")
        output.write(f"Logic: BLK subtracted only from 'S' types. CCVs with RSD > {rsd_high}% (!!) ignored for drift.\n")
        final_res.to_csv(output, index=False)
        
        st.download_button("📥 DOWNLOAD COMPLETE REPORT", output.getvalue(), "ElementaQ_Final_Report.csv", "text/csv")
