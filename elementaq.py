import streamlit as st
import pandas as pd
import io
import re

# --- Configuration ---
APP_NAME = "ElementaQ"
st.set_page_config(page_title=APP_NAME, page_icon="🧪", layout="wide")

st.title(f"🧪 {APP_NAME}")
st.subheader("ICP-OES Data Processing Utility - Stage 1 & 2")

# --- SIDEBAR: ALL LIMITS ---
st.sidebar.header("⚙️ Methodology Settings")

with st.sidebar.expander("RSD & Reporting Limits", expanded=True):
    rsd_limit_low = st.slider("Yellow Flag (!) limit (%)", 1.0, 15.0, 6.0, 0.5)
    rsd_limit_high = st.slider("Red Flag (!!) limit (%)", 5.0, 30.0, 10.0, 0.5)

with st.sidebar.expander("CCV & Drift Correction", expanded=True):
    ccv_deadband = st.number_input("No correction if drift < (%)", 0.0, 5.0, 5.0)
    ccv_max_limit = st.number_input("Fail CCV if drift > (%)", 5.0, 20.0, 10.0)
    mismatch_limit = st.number_input("Sample/CCV Mismatch limit (%)", 5.0, 50.0, 20.0)

# --- Helper Functions ---
def clean_numeric(val):
    if pd.isna(val): return 0.0
    if isinstance(val, str):
        # Remove flags and handle <LQ
        if "<" in val or "n/a" in val: return 0.0
        val = re.sub(r'[!!|!]', '', val)
    try:
        return float(val)
    except:
        return 0.0

def extract_target(label):
    match = re.search(r'_([\d\.]+)$', str(label))
    return float(match.group(1)) if match else None

# --- File Upload ---
uploaded_file = st.file_uploader("Upload source ICP-OES CSV file", type="csv")

if uploaded_file:
    df_raw = pd.read_csv(uploaded_file)
    non_element_cols = ['Category', 'Label', 'Type']
    element_cols = [col for col in df_raw.columns if col not in non_element_cols]
    
    st.info(f"File loaded. {len(element_cols)} elements detected.")

    # --- STAGE 1 ---
    if st.button("🚀 Start Stage 1 (Initial Filtering)"):
        processed_s1 = []
        for i in range(0, len(df_raw), 4):
            if i + 3 >= len(df_raw): break
            block = df_raw.iloc[i : i + 4].copy()
            block['Category'] = block['Category'].str.strip()
            
            label, row_type = str(block['Label'].iloc[0]), str(block['Type'].iloc[0])
            new_row = {'Label': label, 'Type': row_type, 'Original_Index': i}

            for el in element_cols:
                try:
                    avg_v = block[block['Category'] == "Concentration average"][el].values[0]
                    sd_v = float(block[block['Category'] == "Concentration SD"][el].values[0])
                    rsd_v = float(block[block['Category'] == "Concentration RSD"][el].values[0])
                    
                    matrix_mql = sd_v * 10
                    num_avg = clean_numeric(avg_v)
                    
                    if num_avg < matrix_mql:
                        new_row[el] = f"<{round(matrix_mql, 4)}"
                    else:
                        flag = "!!" if rsd_v > rsd_limit_high else ("!" if rsd_v > rsd_limit_low else "")
                        new_row[el] = f"{round(num_avg, 4)}{flag}"
                except:
                    new_row[el] = "n/a"
            processed_s1.append(new_row)
        
        st.session_state['df_s1'] = pd.DataFrame(processed_s1)
        st.session_state['s1_done'] = True

    if st.session_state.get('s1_done'):
        st.write("### Table 1: Filtered Data")
        st.dataframe(st.session_state['df_s1'], use_container_width=True)

        # --- STAGE 2 ---
        if st.button("🚀 Start Stage 2 (Blanks & Drift)"):
            df1 = st.session_state['df_s1'].copy()
            
            # 1. Calculate Analytical Blank (Type: BLK)
            blank_rows = df1[df1['Type'] == 'BLK']
            avg_blanks = {el: blank_rows[el].apply(clean_numeric).mean() if not blank_rows.empty else 0.0 for el in element_cols}

            # 2. Identify CCV points for Drift Factors
            processed_s2 = []
            current_drift_factors = {el: 1.0 for el in element_cols}
            
            for _, row in df1.iterrows():
                new_row = row.to_dict()
                
                # If row is CCV, update drift factors for subsequent samples
                if "CCV" in str(row['Type']):
                    target = extract_target(row['Label'])
                    if target:
                        for el in element_cols:
                            measured = clean_numeric(row[el])
                            if measured > 0:
                                drift_pct = abs((measured - target) / target) * 100
                                if drift_pct <= ccv_deadband:
                                    current_drift_factors[el] = 1.0
                                elif drift_pct <= ccv_max_limit:
                                    current_drift_factors[el] = target / measured
                                else:
                                    current_drift_factors[el] = 1.0 # Fail case, logged in Table 3
                
                # Apply corrections to Samples (Type: S)
                if row['Type'] == 'S':
                    for el in element_cols:
                        raw_val = clean_numeric(row[el])
                        # 1. Drift Correction
                        corrected_val = raw_val * current_drift_factors[el]
                        # 2. Blank Subtraction
                        final_val = corrected_val - avg_blanks.get(el, 0)
                        new_row[el] = round(max(0, final_val), 4)
                
                processed_s2.append(new_row)

            st.session_state['df_s2'] = pd.DataFrame(processed_s2)
            st.session_state['s2_done'] = True

        if st.session_state.get('s2_done'):
            st.success("Stage 2 Complete: Blanks subtracted and Drift corrected.")
            st.write("### Table 2: Final Corrected Concentrations")
            st.dataframe(st.session_state['df_s2'], use_container_width=True)
            
            # Export
            csv_buf = io.StringIO()
            st.session_state['df_s2'].to_csv(csv_buf, index=False)
            st.download_button("📥 Download Final Table 2", csv_buf.getvalue(), "ElementaQ_Final_Results.csv")
