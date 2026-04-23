import streamlit as st
import pandas as pd
import io

# --- UI Configuration ---
st.set_page_config(page_title="ElementaQ", page_icon="🧪", layout="wide")

st.title("🧪 ElementaQ")
st.subheader("ICP-OES Data Processing Utility")

# --- Sidebar: User Defined Thresholds ---
st.sidebar.header("RSD Threshold Settings")
st.sidebar.info("Define limits for stability flags.")

rsd_limit_low = st.sidebar.slider(
    "Yellow Flag (!) limit (%)", 
    min_value=1.0, max_value=15.0, value=6.0, step=0.5
)
rsd_limit_high = st.sidebar.slider(
    "Red Flag (!!) limit (%)", 
    min_value=5.0, max_value=30.0, value=10.0, step=0.5
)

# --- File Upload ---
uploaded_file = st.file_uploader("Upload source ICP-OES CSV file", type="csv")

if uploaded_file:
    # Read the raw data structure
    df_raw = pd.read_csv(uploaded_file)
    
    # Identification of element columns (Ca 317.933 etc)
    non_element_cols = ['Category', 'Label', 'Type']
    element_cols = [col for col in df_raw.columns if col not in non_element_cols]
    
    st.success(f"File '{uploaded_file.name}' loaded. Ready to process {len(element_cols)} elements.")

    # --- Calculation Trigger (MANDATORY BUTTON) ---
    if st.button("🚀 Start Calculations"):
        processed_data = []
        last_inst_mql = {} 

        # Process in blocks of 4 rows (Thermo Standard)
        for i in range(0, len(df_raw), 4):
            if i + 3 >= len(df_raw):
                break
            
            block = df_raw.iloc[i : i + 4].copy()
            block['Category'] = block['Category'].str.strip()
            
            label = str(block['Label'].iloc[0])
            row_type = str(block['Type'].iloc[0])
            
            new_row = {
                'Label': label,
                'Type': row_type
            }

            for el in element_cols:
                try:
                    # Extract values from the block
                    avg_series = block[block['Category'] == "Concentration average"][el]
                    sd_series = block[block['Category'] == "Concentration SD"][el]
                    rsd_series = block[block['Category'] == "Concentration RSD"][el]
                    mql_series = block[block['Category'] == "MQL"][el]

                    if avg_series.empty:
                        continue

                    avg_val = avg_series.values[0]
                    sd_val = float(sd_series.values[0])
                    rsd_val = float(rsd_series.values[0])
                    inst_mql = float(mql_series.values[0])
                    
                    last_inst_mql[el] = inst_mql

                    # Matrix MQL Logic: SD * 10
                    matrix_mql = sd_val * 10
                    
                    # Detection check
                    is_below = False
                    if isinstance(avg_val, str) and "<LQ" in avg_val:
                        is_below = True
                    else:
                        num_avg = float(avg_val)
                        if num_avg < matrix_mql:
                            is_below = True
                    
                    if is_below:
                        new_row[el] = f"<{round(matrix_mql, 4)}"
                    else:
                        # RSD Flags based on Sidebar Sliders
                        if rsd_val > rsd_limit_high:
                            new_row[el] = f"{round(num_avg, 4)}!!"
                        elif rsd_val > rsd_limit_low:
                            new_row[el] = f"{round(num_avg, 4)}!"
                        else:
                            new_row[el] = round(num_avg, 4)
                            
                except (ValueError, IndexError):
                    new_row[el] = "n/a"

            processed_data.append(new_row)

        # Finalize Table 1
        res_df = pd.DataFrame(processed_data)

        # Add Instrumental MQL reference row
        if last_inst_mql:
            mql_ref_row = {'Label': 'MQL (Instrument)', 'Type': 'REF'}
            mql_ref_row.update(last_inst_mql)
            res_df = pd.concat([res_df, pd.DataFrame([mql_ref_row])], ignore_index=True)

        # --- Output UI ---
        st.divider()
        st.write("### Table 1: Filtered Results (ElementaQ)")
        st.dataframe(res_df, use_container_width=True)

        # --- Download Link ---
        output = io.StringIO()
        res_df.to_csv(output, index=False)
        csv_data = output.getvalue()
