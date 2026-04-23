import streamlit as st
import pandas as pd
import io
import re

# --- UI Configuration ---
st.set_page_config(page_title="ICP-OES Data Processor", page_icon="🧪", layout="wide")

st.title("🧪 ICP-OES Analytical Tool - Phase 1")
st.subheader("Data Filtering & Matrix LOQ Validation")

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
    # Read the file
    df_raw = pd.read_csv(uploaded_file)
    
    # Identify element columns (skipping Category, Label, Type)
    non_element_cols = ['Category', 'Label', 'Type']
    element_cols = [col for col in df_raw.columns if col not in non_element_cols]
    
    st.success(f"File uploaded. Detected {len(element_cols)} element/wavelength columns.")

    # --- Calculation Trigger ---
    if st.button("🚀 RUN CALCULATIONS"):
        processed_data = []
        mql_instrumental = {} # To store the last MQL row values

        # Process in blocks of 4 rows
        # Assumes rows: Concentration average, Concentration SD, Concentration RSD, MQL
        for i in range(0, len(df_raw), 4):
            if i + 3 >= len(df_raw):
                break
            
            # Extract the block
            block = df_raw.iloc[i : i + 4].copy()
            
            # Map categories to rows for easy access
            # We strip spaces to avoid matching errors
            block['Category'] = block['Category'].str.strip()
            
            label = str(block['Label'].iloc[0])
            row_type = str(block['Type'].iloc[0])
            
            new_row = {
                'Label': label,
                'Type': row_type
            }

            for el in element_cols:
                try:
                    # Get values from specific rows in the block
                    avg_val = block[block['Category'] == "Concentration average"][el].values[0]
                    sd_val = float(block[block['Category'] == "Concentration SD"][el].values[0])
                    rsd_val = float(block[block['Category'] == "Concentration RSD"][el].values[0])
                    inst_mql = float(block[block['Category'] == "MQL"][el].values[0])
                    
                    #
