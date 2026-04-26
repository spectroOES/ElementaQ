import streamlit as st
import pandas as pd
import numpy as np
import re
from io import BytesIO

# ==================== 1. SETTINGS AND INTERFACE ====================
st.set_page_config(layout="wide", page_title="ElementaQ v14.0")
st.title("⚗️ ElementaQ: ICP-OES Analytical Engine v14.0")
st.caption("Metrology-compliant drift correction with 3-tier filtering & Smart Blank Logic")

def reset_all():
    st.session_state.results = None

if 'results' not in st.session_state:
    st.session_state.results = None

with st.sidebar:
    st.header("🔧 QC Settings")
    
    # RSD Flags as number inputs with clear labels
    rsd_l = st.number_input(
        "Yellow Flag RSD % (Warning)", 
        min_value=1.0, 
        max_value=15.0, 
        value=5.0,
        step=0.5,
        help="RSD threshold for warning flag"
    )
    
    rsd_h = st.number_input(
        "Red Flag RSD % (Critical)", 
        min_value=1.0, 
        max_value=25.0, 
        value=10.0,
        step=0.5,
        help="RSD threshold for critical flag"
    )
    
    st.markdown("---")
    st.header("📊 Drift Calibration (Chapter 3-5)")
    
    fit_window = st.number_input(
        "Filter #1: CCV Match Window (±%)", 
        min_value=5.0, 
        max_value=100.0, 
        value=20.0,
        step=1.0,
        help="Only CCVs with TARGET within sample ± this % are considered"
    )
    
    d_deadband = st.number_input(
        "Tier A: Deadband % (No Correction)", 
        min_value=0.0, 
        max_value=10.0, 
        value=5.0,
        step=0.5,
        help="Drift within this range is considered stable"
    )
    
    d_max = st.number_input(
        "Tier C: Max Drift % (QC FAIL)", 
        min_value=5.0, 
        max_value=50.0, 
        value=10.0,
        step=0.5,
        help="Drift exceeding this value blocks correction"
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

def get_target(type_str):
    """Extracts TARGET concentration from sample name: 'CCV_0.1' → 0.1"""
    match = re.search(r'_([\d.]+)$', str(type_str))
    return float(match.group(1)) if match else None

def calculate_drift_tier(found, target, deadband, max_drift):
    """
    Chapter 3: Three-tiered drift assessment
    Returns: (factor, status_string, is_fail)
    """
    if found is None or target is None or target == 0:
        return 1.0, "Invalid", False
    
    deviation_pct = abs((found - target) / target) * 100
    
    if deviation_pct <= deadband:
        # Tier A: Statistically insignificant drift
        return 1.0, f"Stable({deviation_pct:.1f}%)", False
    elif deviation_pct > max_drift:
        # Tier C: Catastrophic drift — blocking
        return 1.0, f"QC FAIL({deviation_pct:.1f}%)", True
    else:
        # Tier B: Correctable drift
        factor = target / found
        return factor, f"Corrected({deviation_pct:.1f}%)", False

def check_concentration_match(sample_conc, ccv_target, window_pct):
    """
    Filter #1: Concentration Match (Chapter 4)
    Checks: ccv_target ∈ [sample_conc × (1±window/100)]
    """
    if sample_conc is None or ccv_target is None:
        return False
    # Protection against division by zero for zero concentrations
    if sample_conc == 0:
        return ccv_target == 0
    lower = sample_conc * (1 - window_pct / 100)
    upper = sample_conc * (1 + window_pct / 100)
    return lower <= ccv_target <= upper

def interpolate_factor(idx_sample, idx_start, idx_end, f_start, f_end):
    """
    Chapter 5: Linear interpolation formula
    fi = fstart + (fend − fstart) × (i − istart) / (iend − istart)
    """
    if idx_end == idx_start:
        return f_start
    fraction = (idx_sample - idx_start) / (idx_end - idx_start)
    return f_start + (f_end - f_start) * fraction

# ==================== 3. DATA PROCESSING ====================

uploaded_file = st.file_uploader("📁 Upload ICP-OES CSV", type="csv", on_change=reset_all)

if uploaded_file and st.button("🚀 Execute Analysis", type="primary"):
    df = pd.read_csv(uploaded_file)
    df.columns = df.columns.str.strip()
    
    # Identify element columns (exclude metadata)
    metadata_cols = ['Category', 'Label', 'Type']
    elements = [c for c in df.columns if c not in metadata_cols]
    
    # Parse data into blocks of 4 rows: Average | SD | RSD | MQL
    blocks = []
    for i in range(0, len(df) - (len(df) % 4), 4):
        sub = df.iloc[i:i+4]
        try:
            avg_row = sub[sub['Category'].str.contains('average', case=False, na=False)].iloc[0]
            sd_row = sub[sub['Category'].str.contains('SD', case=False, na=False)].iloc[0]
            rsd_row = sub[sub['Category'].str.contains('RSD', case=False, na=False)].iloc[0]
            mql_row = sub[sub['Category'].str.contains('MQL', case=False, na=False)].iloc[0]
            
            blocks.append({
                'idx': i // 4,
                'Label': avg_row['Label'],
                'Type': avg_row['Type'],
                'avg': avg_row, 'sd': sd_row, 'rsd': rsd_row, 'mql': mql_row,
                'f_drift': {}, 'drift_note': {}, 'qc_fail': {}
            })
        except IndexError:
            continue
    
    # === STEP 1: Pre-calculate all CCVs for each element ===
    ccv_registry = {}
    for el in elements:
        ccv_registry[el] = []
        for b in blocks:
            if 'CCV' in str(b['Type']).upper():
                target = get_target(b['Type'])      # TARGET from name (constant)
                found = to_num(b['avg'][el])         # FOUND from detector (measurement)
                
                if target and found is not None:
                    factor, status, is_fail = calculate_drift_tier(
                        found, target, d_deadband, d_max
                    )
                    ccv_registry[el].append({
                        'idx': b['idx'],
                        'target': target,    # For Filters #1 and #3
                        'found': found,      # For factor calculation
                        'factor': factor,
                        'status': status,
                        'qc_fail': is_fail   # For Filter #2
                    })
    
    # === STEP 2: Apply drift correction to each sample ===
    for b in blocks:
        for el in elements:
            raw_conc = to_num(b['avg'][el])
            
            # 🎯 FILTER #1: Concentration Match
            candidates = [
                ccv for ccv in ccv_registry[el]
                if check_concentration_match(raw_conc, ccv['target'], fit_window)
            ]
            
            if not candidates or raw_conc is None:
                b['f_drift'][el] = 1.0
                b['drift_note'][el] = "No Fit"
                b['qc_fail'][el] = False
                continue
            
            # Find nearest CCV BEFORE and AFTER sample (by index)
            before = [c for c in candidates if c['idx'] <= b['idx']]
            after = [c for c in candidates if c['idx'] >= b['idx']]
            nearest_before = max(before, key=lambda x: x['idx']) if before else None
            nearest_after = min(after, key=lambda x: x['idx']) if after else None
            
            # 🎯 FILTER #2: Tier C Failure Check
            if (nearest_before and nearest_before['qc_fail']) or \
               (nearest_after and nearest_after['qc_fail']):
                b['f_drift'][el] = 1.0
                b['drift_note'][el] = "QC FAIL"
                b['qc_fail'][el] = True
                continue
            
            # 🎯 FILTER #3: Identical Aliquot Rule
            if nearest_before and nearest_after:
                # Both standards found — check TARGET identity
                if abs(nearest_before['target'] - nearest_after['target']) < 1e-9:
                    # ✅ Identical targets → linear interpolation (Chapter 5)
                    f_interp = interpolate_factor(
                        b['idx'],
                        nearest_before['idx'], nearest_after['idx'],
                        nearest_before['factor'], nearest_after['factor']
                    )
                    b['f_drift'][el] = f_interp
                    b['drift_note'][el] = f"Interp({nearest_before['target']})"
                else:
                    # ❌ Different targets → fallback to Single Point Correction (Chapter 4)
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
    
    # === STEP 3: Calculate average blank (CORRECTED LOGIC) ===
    # CORRECTION: Check original value for "<" BEFORE conversion to number
    avg_blanks = {}
    for el in elements:
        valid_blanks = []
        for b in blocks:
            t = str(b['Type']).upper()
            if any(x in t for x in ['BLK', 'MBB', 'REAGENT']):
                # 🔍 Get ORIGINAL value from CSV as string
                raw_value_str = str(b['avg'][el])
                
                # ✅ CRITICAL CHECK: if "<" present — SKIP this blank!
                if '<' in raw_value_str:
                    continue
                
                # Only if no "<", convert to number
                blank_val = to_num(raw_value_str)
                if blank_val is not None:
                    f = b['f_drift'].get(el, 1.0)
                    valid_blanks.append(blank_val * f)
        
        # If no valid blanks — average equals 0
        avg_blanks[el] = np.mean(valid_blanks) if valid_blanks else 0.0
    
    # === STEP 4: Generate three output tables ===
    t1_rows, t2_rows, t3_rows = [], [], []
    
    for b in blocks:
        # ── TABLE 1: Detection Thresholds and LOQ ──
        row1 = {'Label': b['Label'], 'Type': b['Type']}
        loq_flags = {}
        
        for el in elements:
            raw_v = to_num(b['avg'][el])
            mql_v = to_num(b['mql'][el]) or 0.0
            sd_v = to_num(b['sd'][el]) or 0.0
            loq_threshold = max(mql_v, sd_v * 10)  # More conservative threshold
            
            is_below_loq = (raw_v is None) or (raw_v < loq_threshold) or ('<' in str(b['avg'][el]))
            
            if is_below_loq:
                row1[el] = f"<{loq_threshold:.4f}"
                loq_flags[el] = loq_threshold  # Remember for Hard Lock
            else:
                rsd_v = to_num(b['rsd'][el]) or 0.0
                flag = "!!" if rsd_v > rsd_h else ("!" if rsd_v > rsd_l else "")
                row1[el] = f"{raw_v:.4f}{flag}"
                loq_flags[el] = None
        t1_rows.append(row1)
        
        # ── TABLES 2 & 3: Only for samples (type starts with 'S') ──
        if str(b['Type']).startswith('S'):
            row2 = {'Label': b['Label']}  # Final results
            row3 = {'Label': b['Label']}  # Math log (audit)
            dilution = get_target(b['Type']) or 1.0
            
            for el in elements:
                if loq_flags[el] is not None:
                    # 🔒 HARD LOCK: Below LOQ — only dilution, no drift or blank
                    row2[el] = f"<{loq_flags[el] * dilution:.4f}"
                    row3[el] = f"LOQ<{loq_flags[el]:.4f} × Dil{dilution} [LOCKED]"
                else:
                    # 📐 Full formula: ((Raw × Drift) − Blank) × Dilution
                    v_raw = to_num(b['avg'][el])
                    f_drift = b['f_drift'].get(el, 1.0)
                    blank_avg = avg_blanks[el]
                    
                    final_val = ((v_raw * f_drift) - blank_avg) * dilution
                    row2[el] = f"{final_val:.4f}"
                    
                    # Detailed calculation log
                    note = b['drift_note'].get(el, 'N/A')
                    qc_mark = "[QC FAIL] " if b['qc_fail'].get(el, False) else ""
                    row3[el] = f"{qc_mark}(({v_raw:.3f}×{f_drift:.3f}[{note}])−{blank_avg:.3f}[BLK])×{dilution}"
            
            t2_rows.append(row2)
            t3_rows.append(row3)
    
    st.session_state.results = (
        pd.DataFrame(t1_rows),
        pd.DataFrame(t2_rows),
        pd.DataFrame(t3_rows)
    )

# ==================== 4. OUTPUT AND EXPORT ====================
if st.session_state.results:
    t1, t2, t3 = st.session_state.results
    
    # 📥 Excel export with formatting
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        t1.to_excel(writer, sheet_name='Report', startrow=1, index=False)
        t2.to_excel(writer, sheet_name='Report', startrow=len(t1)+5, index=False)
        t3.to_excel(writer, sheet_name='Report', startrow=len(t1)+len(t2)+9, index=False)
        ws = writer.sheets['Report']
        ws.write(0, 0, "TABLE 1: Detection Thresholds")
        ws.write(len(t1)+4, 0, "TABLE 2: Final Results")
        ws.write(len(t1)+len(t2)+8, 0, "TABLE 3: Audit Trail")
    
    st.download_button("📥 Download XLSX Report", buffer.getvalue(), "ElementaQ_Report.xlsx")
    
    # 🖥️ Display in Streamlit
    with st.expander("📋 Table 1: Thresholds & LOQ", expanded=True):
        st.dataframe(t1, use_container_width=True, hide_index=True)
    
    with st.expander("✅ Table 2: Final Results", expanded=True):
        st.dataframe(t2, use_container_width=True, hide_index=True)
    
    with st.expander("🔍 Table 3: Math Log (Audit Trail)", expanded=True):
        st.dataframe(t3, use_container_width=True, hide_index=True)
        st.caption("Format: ((Raw×Factor[Note])−Blank[BLK])×Dilution")
    
    # 📊 QC Summary
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
