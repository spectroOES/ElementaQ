import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO

# ==================== CUSTOM CSS STYLING (DESIGNER EDITION) ====================
st.markdown("""
<style>
    /* General Button Styling */
    .stButton > button {
        border-radius: 12px;
        font-weight: bold;
        transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
        padding: 0.6rem 1.2rem;
        letter-spacing: 0.5px;
    }

    /* Primary Button (Execute Analysis) - Green Gradient & Levitation */
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #00b09b 0%, #96c93d 100%);
        color: white;
        border: none;
        box-shadow: 0 4px 15px rgba(0, 176, 155, 0.3);
    }
    
    .stButton > button[kind="primary"]:hover {
        transform: translateY(-3px);
        box-shadow: 0 8px 25px rgba(0, 176, 155, 0.5);
        background: linear-gradient(135deg, #00c4ad 0%, #a8e04d 100%);
    }

    .stButton > button[kind="primary"]:active {
        transform: translateY(1px);
        box-shadow: 0 2px 10px rgba(0, 176, 155, 0.2);
    }

    /* Secondary Button (Download) - Light Blue Modern Look */
    .stButton > button[kind="secondary"] {
        background-color: #e3f2fd;
        color: #1565c0;
        border: 2px solid #bbdefb;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
    }

    .stButton > button[kind="secondary"]:hover {
        background-color: #bbdefb;
        border-color: #90caf9;
        transform: scale(1.03);
        color: #0d47a1;
    }

    /* File Uploader Styling */
    .uploadedFile {
        border-radius: 10px;
        border: 2px dashed #ccc;
        padding: 20px;
        text-align: center;
        background-color: #fafafa;
    }
</style>
""", unsafe_allow_html=True)

# ==================== 1. SETTINGS AND INTERFACE ====================
st.set_page_config(layout="wide", page_title="ElementaQ v14.3")
st.title("⚗️ ElementaQ: ICP-OES Analytical Engine v14.3")
st.caption("Metrology-compliant drift correction with robust column detection")

def reset_all():
    st.session_state.results = None

if 'results' not in st.session_state:
    st.session_state.results = None

with st.sidebar:
    st.header("🔧 QC Settings")
    
    # RSD Flags
    rsd_l = st.number_input(
        "Yellow Flag RSD % (Warning)", 
        min_value=1.0, max_value=15.0, value=5.0, step=0.5,
        help="RSD threshold for warning flag"
    )
    
    rsd_h = st.number_input(
        "Red Flag RSD % (Critical)", 
        min_value=1.0, max_value=25.0, value=10.0, step=0.5,
        help="RSD threshold for critical flag"
    )
    
    st.markdown("---")
    st.header("📊 Drift Calibration")
    
    fit_window = st.number_input(
        "Filter #1: CCV Match Window (±%)", 
        min_value=5.0, max_value=200.0, value=50.0, step=5.0,
        help="Recommended: 50% for ICP-OES"
    )
    
    d_deadband = st.number_input(
        "Tier A: Deadband % (No Correction)", 
        min_value=0.0, max_value=10.0, value=5.0, step=0.5,
        help="Drift within this range is stable"
    )
    
    d_max = st.number_input(
        "Tier C: Max Drift % (QC FAIL)", 
        min_value=5.0, max_value=50.0, value=10.0, step=0.5,
        help="Drift exceeding this blocks correction"
    )
    
    st.info("📌 Filter #3: Interpolation requires IDENTICAL CCV targets")

# ==================== 2. HELPER FUNCTIONS ====================

def to_num(val):
    """Converts CSV value to float, handling <LQ, N/A, etc."""
    if pd.isna(val):
        return None
    try:
        s = re.sub(r'[<>!]', '', str(val)).strip()
        return float(s)
    except:
        return None

def find_column_name(df, possible_names):
    """Finds the first column name that matches any of the possible names (case-insensitive)."""
    cols_lower = {c.lower(): c for c in df.columns}
    for name in possible_names:
        if name.lower() in cols_lower:
            return cols_lower[name.lower()]
    return None

def is_yttrium_column(col_name):
    """Checks if column is Yttrium (Internal Standard)."""
    return str(col_name).strip().startswith('Y ')

def get_target(type_str):
    """Extracts TARGET concentration from CCV/ICV name: 'CCV_0.1' → 0.1"""
    match = re.search(r'_([\d.]+)$', str(type_str))
    return float(match.group(1)) if match else None

def get_dilution_factor(type_str):
    """Extracts dilution factor from sample Type. Pattern: S_dil100 → 100.0"""
    type_str = str(type_str).strip()
    if not type_str.upper().startswith('S'):
        return 1.0
    match = re.search(r'_dil(\d+(?:\.\d+)?)$', type_str, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return 1.0

def calculate_drift_tier(found, target, deadband, max_drift):
    """Three-tiered drift assessment."""
    if found is None or target is None or target == 0:
        return 1.0, "Invalid", False
    
    deviation_pct = abs((found - target) / target) * 100
    
    if deviation_pct <= deadband:
        return 1.0, f"Stable({deviation_pct:.1f}%)", False
    elif deviation_pct > max_drift:
        return 1.0, f"QC FAIL({deviation_pct:.1f}%)", True
    else:
        factor = target / found
        return factor, f"Corrected({deviation_pct:.1f}%)", False

def check_concentration_match(sample_conc, ccv_target, window_pct):
    """Filter #1: Concentration Match."""
    if sample_conc is None or ccv_target is None:
        return False
    if sample_conc == 0:
        return ccv_target == 0
    lower = sample_conc * (1 - window_pct / 100)
    upper = sample_conc * (1 + window_pct / 100)
    return lower <= ccv_target <= upper

def interpolate_factor(idx_sample, idx_start, idx_end, f_start, f_end):
    """Linear interpolation formula."""
    if idx_end == idx_start:
        return f_start
    fraction = (idx_sample - idx_start) / (idx_end - idx_start)
    return f_start + (f_end - f_start) * fraction

# ==================== 3. DATA PROCESSING ====================

uploaded_file = st.file_uploader("📁 Upload ICP-OES CSV", type="csv", on_change=reset_all)

if uploaded_file and st.button("🚀 Execute Analysis", type="primary"):
    df = pd.read_csv(uploaded_file)
    df.columns = df.columns.str.strip()
    
    # 🔍 ROBUST COLUMN DETECTION & VALIDATION
    type_col_name = find_column_name(df, ['Type', 'Sample Type', 'Type of Sample', 'Sample_Type'])
    label_col_name = find_column_name(df, ['Label', 'Sample Name', 'Name', 'Sample_Label'])
    category_col_name = find_column_name(df, ['Category', 'Parameter', 'Analyte'])
    
    if not type_col_name or not label_col_name or not category_col_name:
        st.error("❌ **Invalid File Format**")
        st.markdown("""
        Your file is out of standard format. The required columns were not found.
        
        **Please format your data table according to instructions presented on this page, then upload.**
        
        *Required Columns:*
        - **Type** (or 'Sample Type'): e.g., S, BLK, CCV_0.1
        - **Label** (or 'Sample Name'): e.g., smp 1, blank icp
        - **Category** (or 'Parameter'): Must contain rows for 'Average', 'SD', 'RSD', 'MQL'
        """)
        st.stop()

    metadata_cols = [category_col_name, label_col_name, type_col_name]
    elements = [c for c in df.columns if c not in metadata_cols]
    
    blocks = []
    for i in range(0, len(df) - (len(df) % 4), 4):
        sub = df.iloc[i:i+4]
        try:
            avg_row = sub[sub[category_col_name].str.contains('average', case=False, na=False)].iloc[0]
            sd_row = sub[sub[category_col_name].str.contains('SD', case=False, na=False)].iloc[0]
            rsd_row = sub[sub[category_col_name].str.contains('RSD', case=False, na=False)].iloc[0]
            mql_row = sub[sub[category_col_name].str.contains('MQL', case=False, na=False)].iloc[0]
            
            blocks.append({
                'idx': i // 4,
                'Label': avg_row[label_col_name],
                'Type': avg_row[type_col_name],
                'avg': avg_row, 'sd': sd_row, 'rsd': rsd_row, 'mql': mql_row,
                'f_drift': {}, 'drift_note': {}, 'qc_fail': {}
            })
        except IndexError:
            continue
    
    if not blocks:
        st.error("❌ No data blocks found. Check if 'Category' column contains 'average', 'SD', 'RSD', 'MQL'.")
        st.stop()

    # === STEP 1: Pre-calculate all CCVs for each element ===
    ccv_registry = {}
    for el in elements:
        ccv_registry[el] = []
        for b in blocks:
            if 'CCV' in str(b['Type']).upper():
                target = get_target(b['Type'])
                found = to_num(b['avg'][el])
                
                if target and found is not None:
                    factor, status, is_fail = calculate_drift_tier(
                        found, target, d_deadband, d_max
                    )
                    ccv_registry[el].append({
                        'idx': b['idx'],
                        'target': target,
                        'found': found,
                        'factor': factor,
                        'status': status,
                        'qc_fail': is_fail
                    })
    
    # === STEP 2: Apply drift correction to each sample ===
    for b in blocks:
        for el in elements:
            raw_conc = to_num(b['avg'][el])
            
            candidates = [
                ccv for ccv in ccv_registry[el]
                if check_concentration_match(raw_conc, ccv['target'], fit_window)
            ]
            
            if not candidates or raw_conc is None:
                b['f_drift'][el] = 1.0
                b['drift_note'][el] = "No Fit"
                b['qc_fail'][el] = False
                continue
            
            before = [c for c in candidates if c['idx'] <= b['idx']]
            after = [c for c in candidates if c['idx'] >= b['idx']]
            nearest_before = max(before, key=lambda x: x['idx']) if before else None
            nearest_after = min(after, key=lambda x: x['idx']) if after else None
            
            if (nearest_before and nearest_before['qc_fail']) or \
               (nearest_after and nearest_after['qc_fail']):
                b['f_drift'][el] = 1.0
                b['drift_note'][el] = "QC FAIL"
                b['qc_fail'][el] = True
                continue
            
            if nearest_before and nearest_after:
                if abs(nearest_before['target'] - nearest_after['target']) < 1e-9:
                    f_interp = interpolate_factor(
                        b['idx'],
                        nearest_before['idx'], nearest_after['idx'],
                        nearest_before['factor'], nearest_after['factor']
                    )
                    b['f_drift'][el] = f_interp
                    b['drift_note'][el] = f"Interp({nearest_before['target']})"
                else:
                    nearest = min(
                        [c for c in [nearest_before, nearest_after] if c],
                        key=lambda x: abs(x['idx'] - b['idx'])
                    )
                    b['f_drift'][el] = nearest['factor']
                    b['drift_note'][el] = f"SinglePt({nearest['target']})"
            elif nearest_before:
                b['f_drift'][el] = nearest_before['factor']
                b['drift_note'][el] = f"SinglePt({nearest_before['target']})"
            elif nearest_after:
                b['f_drift'][el] = nearest_after['factor']
                b['drift_note'][el] = f"SinglePt({nearest_after['target']})"
            else:
                b['f_drift'][el] = 1.0
                b['drift_note'][el] = "No Bracket"
    
    # === STEP 3: Calculate average blank ===
    avg_blanks = {}
    for el in elements:
        valid_blanks = []
        for b in blocks:
            t = str(b['Type']).upper()
            if any(x in t for x in ['BLK', 'MBB', 'REAGENT']):
                raw_value_str = str(b['avg'][el])
                if '<' in raw_value_str:
                    continue
                blank_val = to_num(raw_value_str)
                if blank_val is not None:
                    f = b['f_drift'].get(el, 1.0)
                    valid_blanks.append(blank_val * f)
        avg_blanks[el] = np.mean(valid_blanks) if valid_blanks else 0.0
    
    # === STEP 4: Generate three output tables ===
    t1_rows, t2_rows, t3_rows = [], [], []
    
    for b in blocks:
        row1 = {'Label': b['Label'], 'Type': b['Type']}
        loq_flags = {}
        
        for el in elements:
            raw_v = to_num(b['avg'][el])
            sd_v = to_num(b['sd'][el]) or 0.0
            loq_from_sd = sd_v * 10
            
            raw_str = str(b['avg'][el])
            is_below_loq = (raw_v is None) or (raw_v < 0) or ('<' in raw_str)
            
            if is_below_loq:
                row1[el] = f"<{loq_from_sd:.4f}"
                loq_flags[el] = loq_from_sd
            else:
                rsd_v = to_num(b['rsd'][el]) or 0.0
                flag = "!!" if rsd_v > rsd_h else ("!" if rsd_v > rsd_l else "")
                row1[el] = f"{raw_v:.4f}{flag}"
                loq_flags[el] = None
        t1_rows.append(row1)
        
        if str(b['Type']).startswith('S'):
            row2 = {'Label': b['Label']}
            row3 = {'Label': b['Label']}
            dilution = get_dilution_factor(b['Type'])
            
            for el in elements:
                if loq_flags[el] is not None:
                    row2[el] = f"<{loq_flags[el] * dilution:.4f}"
                    row3[el] = f"LOQ<{loq_flags[el]:.4f} × Dil{dilution} [LOCKED]"
                else:
                    v_raw = to_num(b['avg'][el])
                    f_drift = b['f_drift'].get(el, 1.0)
                    
                    if is_yttrium_column(el):
                        blank_avg = 0.0
                        blank_note = "NO BLK"
                    else:
                        blank_avg = avg_blanks[el]
                        blank_note = "BLK"
                    
                    final_val = ((v_raw * f_drift) - blank_avg) * dilution
                    row2[el] = f"{final_val:.4f}"
                    
                    note = b['drift_note'].get(el, 'N/A')
                    qc_mark = "[QC FAIL] " if b['qc_fail'].get(el, False) else ""
                    row3[el] = f"{qc_mark}(({v_raw:.3f}×{f_drift:.3f}[{note}])−{blank_avg:.3f}[{blank_note}])×{dilution}"
            
            t2_rows.append(row2)
            t3_rows.append(row3)
    
    # Add MQL reference row
    mql_row = {'Label': 'MQL (Reference)', 'Type': 'System'}
    for el in elements:
        if blocks:
            mql_val = to_num(blocks[0]['mql'][el])
            mql_row[el] = f"{mql_val:.4f}" if mql_val is not None else "N/A"
        else:
            mql_row[el] = "N/A"
    t1_rows.append(mql_row)
    
    st.session_state.results = (
        pd.DataFrame(t1_rows),
        pd.DataFrame(t2_rows),
        pd.DataFrame(t3_rows)
    )

# ==================== 4. OUTPUT AND EXPORT ====================
if st.session_state.results:
    t1, t2, t3 = st.session_state.results
    
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        t1.to_excel(writer, sheet_name='Report', startrow=1, index=False)
        t2.to_excel(writer, sheet_name='Report', startrow=len(t1)+5, index=False)
        t3.to_excel(writer, sheet_name='Report', startrow=len(t1)+len(t2)+9, index=False)
        ws = writer.sheets['Report']
        ws.write(0, 0, "TABLE 1: Detection Thresholds & LOQ (SD×10)")
        ws.write(len(t1)+4, 0, "TABLE 2: Final Results")
        ws.write(len(t1)+len(t2)+8, 0, "TABLE 3: Audit Trail")
    
    st.download_button("📥 Download XLSX Report", buffer.getvalue(), "ElementaQ_Report.xlsx", type="secondary")
    
    with st.expander("📋 Table 1: Thresholds & LOQ", expanded=True):
        st.dataframe(t1, use_container_width=True, hide_index=True)
    
    with st.expander("✅ Table 2: Final Results", expanded=True):
        st.dataframe(t2, use_container_width=True, hide_index=True)
    
    with st.expander("🔍 Table 3: Math Log (Audit Trail)", expanded=True):
        st.dataframe(t3, use_container_width=True, hide_index=True)
        st.caption("Format: ((Raw×Factor[Note])−Blank[BLK/NO BLK])×Dilution")
    
    st.markdown("---")
    col1, col2, col3 = st.columns(3)
    with col1:
        yellow = sum(1 for c in t1.columns if c not in ['Label','Type'] 
                    for v in t1[c] if '!' in str(v) and '!!' not in str(v))
        st.metric("🟡 Warnings", yellow)
    with col2:
        red = sum(1 for c in t1.columns if c not in ['Label','Type'] 
                 for v in t1[c] if '!!' in str(v))
        st.metric("🔴 High RSD", red)
    with col3:
        fail = sum(1 for b in blocks for el in elements if b['qc_fail'].get(el, False))
        st.metric("⚠️ QC FAIL", fail)
