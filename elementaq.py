import streamlit as st
import pandas as pd
import io

# --- QA Check: App Name initialization ---
APP_NAME = "ElementaQ"

st.set_page_config(page_title=APP_NAME, page_icon="🧪", layout="wide")

# --- UI Header ---
st.title(f"🧪 {APP_NAME}")
st.subheader("ICP-OES Data Processing Utility")

# --- Sidebar Configuration ---
st.sidebar.header("RSD Threshold Settings")
rsd_limit_low = st.sidebar.slider(
    "Yellow Flag (!) limit (%)", 
    min_value=1.0, max_value=15.0, value=6.0, step=0.5
)
rsd_limit_high = st.sidebar.slider(
    "Red Flag (!!) limit (%)", 
    min_value=5.0, max_value=30.0, value=10.0, step=0.5
)

# --- File Upload Section ---
uploaded_file = st.file_uploader("Upload source ICP-OES CSV file", type="csv")

if uploaded_file:
    # QA Simulation: User uploads file, we read headers but STOP here.
    df_raw = pd.read_csv(uploaded_file)
    
    non_element_cols = ['Category', 'Label', 'Type']
    element_cols = [col for col in df_raw.columns if col not in non_element_cols]
    
    st.success(f"File '{uploaded_file.name}' loaded. Ready to analyze {len(element_cols)} elements.")
    
    # --- The Trigger Button (Requirement: Manual Start) ---
    if st.button("🚀 Start Calculations"):
        processed_data = []
        last_inst_mql = {} 

        # Processing 4-row blocks (Thermo format)
        for i in range(0, len(df_raw), 4):
            if i + 3 >= len(df_raw):
                break
            
            block = df_raw.iloc[i : i + 4].copy()
            block['Category'] = block['Category'].str.strip()
            
            # Using the first row of the block for ID
            label = str(block['Label'].iloc[0])
            row_type = str(block['Type'].iloc[0])
            
            new_row = {
                'Label': label,
                'Type': row_type
            }

            for el in element_cols:
                try:
                    # Access data using Category filter
                    avg_val = block[block['Category'] == "Concentration average"][el].values[0]
                    sd_val = float(block[block['Category'] == "Concentration SD"][el].values[0])
                    rsd_val = float(block[block['Category'] == "Concentration RSD"][el].values[0])
                    inst_mql = float(block[block['Category'] == "MQL"][el].values[0])
                    
                    last_inst_mql[el] = inst_mql

                    # Matrix LOQ Logic: SD * 10
                    matrix_mql = sd_val * 10
                    
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
                        # Applying RSD flags from sidebar
                        if rsd_val > rsd_limit_high:
                            new_row[el] = f"{round(num_avg, 4)}!!"
                        elif rsd_val > rsd_limit_low:
                            new_row[el] = f"{round(num_avg, 4)}!"
                        else:
                            new_row[el] = round(num_avg, 4)
                            
                except (ValueError, IndexError):
                    new_row[el] = "n/a"

            processed_data.append(new_row)

        # Build Final Result Table
        res_df = pd.DataFrame(processed_data)

        # Add Reference MQL Row
        if last_inst_mql:
            mql_ref_row = {'Label': 'MQL (Instrument)', 'Type': 'REF'}
            mql_ref_row.update(last_inst_mql)
            res_df = pd.concat([res_df, pd.DataFrame([mql_ref_row])], ignore_index=True)

        # --- Output and Download ---
        st.divider()
        st.write(f"### Results Table (Processed by {APP_NAME})")
        st.dataframe(res_df, use_container_width=True)

        # Prepare CSV for download
        output = io.StringIO()
        res_df.to_csv(output, index=False)
        csv_data = output.getvalue()

        st.download_button(
            label="📥 Download CSV Results",
            data=csv_data,
            file_name="ElementaQ_Table1.csv",
            mime="text/csv"
        )
