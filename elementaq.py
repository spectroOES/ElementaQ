import streamlit as st
import pandas as pd
import re
import io
import numpy as np

# --- 1. CONFIGURATION & UI ---
st.set_page_config(page_title="ElementaQ Pro", layout="wide")
st.title("🧪 ElementaQ: Analytical Engine v7.0")

# Persistent Sidebar Widgets
with st.sidebar:
    st.header("⚙️ Quality Control")
    rsd_l = st.slider("Yellow Flag RSD %", 1.0, 15.0, 6.0, key='rsd_l')
    rsd_h = st.slider("Red Flag RSD %", 5.0, 30.0, 10.0, key='rsd_h')
    st.markdown("---")
    st.header("📈 Drift Settings")
    db_limit = st.number_input("Deadband (No correction < %)", 0.0, 10.0, 5.0, key='db_val')
    max_corr = st.number_input("Max Correction Limit (%)", 5.0, 50.0, 20.0, key='max_c')

# --- 2. HELPER FUNCTIONS ---
def clean_numeric(val):
    if pd.isna(val): return 0.0
    if isinstance(val, str):
        val = re.sub(r'[^\d\.]', '', val.split('<')[0])
    try: return float(val)
    except: return 0.0

def get_nominal(type_str):
    """Extracts numeric target from Type like CCV_0.1 -> 0.1"""
    match = re.search(r'_([\d\.]+)$', str(type_str))
    return float(match.group(1)) if match else None

# --- 3. CORE PROCESSING ---
uploaded_file = st.file_uploader("Upload CSV Data File", type="csv")

if uploaded_file:
    df_raw = pd.read_csv(uploaded_file)
    # Identify data columns (skip metadata)
    data_cols = [c for c in df_raw.columns if c not in ['Category', 'Label', 'Type']]
    
    if st.button("🚀 Run Analysis"):
        # STEP 1: Aggregation (Table 1) - Group by 4 rows
        table1_data = []
        for i in range(0, len(df_raw), 4):
            if i + 3 >= len(df_raw): break
            chunk = df_raw.iloc[i : i+4]
            label = str(chunk['Label'].iloc[0])
            rtype = str(chunk['Type'].iloc[0])
            
            new_row = {'Label': label, 'Type': rtype}
            for col in data_cols:
                # Target row is "Concentration average"
                val = clean_numeric(chunk[chunk['Category'].str.strip() == "Concentration average"][col].values[0])
                new_row[col] = val
            table1_data.append(new_row)
        
        df_s1 = pd.DataFrame(table1_data)

        # STEP 2: Calculate Session Drift Factors (Global f)
        drift_factors = {col: [] for col in data_cols}
        for _, row in df_s1.iterrows():
            target = get_nominal(row['Type'])
            if "CCV" in str(row['Type']) and target:
                for col in data_cols:
                    measured = row[col]
                    if measured > 0:
                        diff = abs((measured - target) / target) * 100
                        # Apply correction only if error is between deadband and max limit
                        if db_limit < diff <= max_corr:
                            drift_factors[col].append(target / measured)
        
        # Average the factors for each column
        final_f = {col: (np.mean(factors) if factors else 1.0) for col, factors in drift_factors.items()}

        # STEP 3: Final Calculations (Table 2 & 3)
        # Global Blank average
        avg_blanks = {col: df_s1[df_s1['Type'] == 'BLK'][col].mean() if not df_s1[df_s1['Type'] == 'BLK'].empty else 0.0 for col in data_cols}
        
        table2_rows = []
        table3_rows = []

        for _, row in df_s1.iterrows():
            rtype = str(row['Type'])
            label = str(row['Label'])
            t2_row = {'Label': label, 'Type': rtype}
            t3_row = {'Label': label}
            
            # Dilution Logic
            dil = 1
            if '_dil' in label:
                d_match = re.search(r'_dil(\d+)', label)
                if d_match: dil = int(d_match.group(1))

            for col in data_cols:
                raw_val = row[col]
                f = final_f[col]
                
                # Blank subtraction applied ONLY to Samples (S)
                blk = avg_blanks.get(col, 0) if rtype == 'S' else 0.0
                
                # Calculation: (Measured * Drift - Blank) * Dilution
                res = round(max(0, (raw_val * f) - blk) * dil, 4)
                t2_row[col] = res
                
                # Audit Trail (S, MBB, BLK)
                if rtype in ['S', 'MBB', 'BLK']:
                    f_str = f"{f:.3f}" if f != 1.0 else "1"
                    t3_row[col] = f"({raw_val}*{f_str}-{blk:.3f})*{dil}"

            table2_rows.append(t2_row)
            if rtype in ['S', 'MBB', 'BLK']:
                table3_rows.append(t3_row)

        st.session_state['processed'] = (df_s1, pd.DataFrame(table2_rows), pd.DataFrame(table3_rows), final_f)

    # --- 4. OUTPUT DISPLAY ---
    if 'processed' in st.session_state:
        s1, s2, s3, f_applied = st.session_state['processed']
        
        # Excel Export
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
            s1.to_excel(writer, sheet_name='1_RawData', index=False)
            s2.to_excel(writer, sheet_name='2_FinalResults', index=False)
            s3.to_excel(writer, sheet_name='3_AuditTrail', index=False)
        
        st.download_button(
            label="📥 Download Excel Report",
            data=buf.getvalue(),
            file_name="ElementaQ_Analysis.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        # Result Tabs
        tab1, tab2, tab3 = st.tabs(["📊 Table 1: Raw", "✅ Table 2: Final", "🔍 Table 3: Audit"])
        
        with tab1:
            st.dataframe(s1, use_container_width=True)
        with tab2:
            st.dataframe(s2, use_container_width=True)
        with tab3:
            st.info(f"Applied Drift Factors (f): { {k: round(v,4) for k,v in f_applied.items() if v != 1.0} }")
            st.dataframe(s3, use_container_width=True)
