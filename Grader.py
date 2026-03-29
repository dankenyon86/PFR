import streamlit as st
import pandas as pd
import requests
import re
from thefuzz import fuzz

# --- 1. CONFIG ---
st.set_page_config(page_title="PFR Candidate Checker", layout="wide")

# --- 2. SECURITY GATE ---
if "password_correct" not in st.session_state:
    st.session_state["password_correct"] = False

if not st.session_state["password_correct"]:
    st.title("🔐 PFR Internal Security")
    pw = st.text_input("Enter PFR Access Code:", type="password")
    if st.button("Unlock Checker"):
        if pw == "BruceWillis":
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("❌ Incorrect password.")
    st.stop()

# --- 3. APP START ---
st.title("🕵️ PFR Candidate Checker")

# --- 4. SMART API FUNCTIONS ---
def check_ipqs_phone(phone_number, api_key):
    """Cleans and validates phone numbers via IPQualityScore API."""
    if not api_key or pd.isna(phone_number): 
        return None
    
    # Clean: Remove everything except digits
    clean_num = re.sub(r'\D', '', str(phone_number))
    
    # Format for UK: Convert 07... to 447...
    if clean_num.startswith('07') and len(clean_num) == 11:
        clean_num = '44' + clean_num[1:]
    elif clean_num.startswith('7') and len(clean_num) == 10:
        clean_num = '44' + clean_num
    
    url = f"https://www.ipqualityscore.com/api/json/phone/validate/{api_key}/{clean_num}"
    try:
        res = requests.get(url, timeout=5).json()
        return res if res.get('success') else None
    except: 
        return None

def normalize(text):
    return re.sub(r'[^a-z0-9]', '', str(text).lower()).strip()

# --- 5. LOADERS ---
col1, col2 = st.columns(2)
with col1:
    resp_file = st.file_uploader("1. Upload Call List (Data)", type=["csv", "xlsx"])
with col2:
    screen_file = st.file_uploader("2. Upload PFR Screener (Logic)", type=["xlsx"])

# --- 6. SIDEBAR SETTINGS ---
st.sidebar.header("⚙️ Logic Settings")
ipqs_key = "H67E8mmH292LeSaTgbrufW5qzj68VEnG" 
reject_voip = st.sidebar.checkbox("Auto-Reject VOIP", value=True)
fuzzy_threshold = st.sidebar.slider("Fuzzy Match Sensitivity %", 80, 100, 95)

if st.sidebar.button("Logout"):
    st.session_state["password_correct"] = False
    st.rerun()

# --- 7. MAIN LOGIC ---
if resp_file and screen_file:
    try:
        # Load Data + BOM Fix
        if resp_file.name.endswith('.csv'):
            df_resp = pd.read_csv(resp_file, encoding='utf-8-sig')
        else:
            df_resp = pd.read_excel(resp_file)
        
        # Sanitize Headers
        df_resp.columns = [str(c).replace('\ufeff', '').replace('ï»¿', '').strip() for c in df_resp.columns]
        headers = df_resp.columns.tolist()
        p_id_col = next((c for c in ['Participant ID', 'ID', 'Ref'] if c in df_resp.columns), headers[0])

        # Find Phone Column
        phone_cols = ['Mobile', 'Phone', 'Telephone', 'Tel', 'Mobile Number', 'Phone Number']
        actual_phone_col = next((c for c in phone_cols if c in headers), None)

        # Load Screener
        raw_screen = pd.read_excel(screen_file, header=None)
        h_idx = next(i for i, row in raw_screen.iterrows() if str(row[0]).strip().lower() in ["question", "questions"])
        df_screen = pd.read_excel(screen_file, header=h_idx)
        df_screen.iloc[:, 0] = df_screen.iloc[:, 0].ffill()
        q_col, a_col = df_screen.columns[0], df_screen.columns[1]
        so_col = next((c for c in df_screen.columns if any(k in str(c).lower() for k in ["screen-out", "disqualify"])), None)
        logic_df = df_screen.dropna(subset=[a_col])

        # --- STEP 1: MAPPING ---
        st.header("⚙️ Step 1: Screener Logic Mapping")
        final_rules, mapping = {}, {}
        for q_text in logic_df[q_col].unique():
            q_rows = logic_df[logic_df[q_col] == q_text]
            options = [str(o).strip() for o in q_rows[a_col].unique().tolist() if pd.notna(o)]
            q_id = re.search(r'q\d+', str(q_text).lower()).group(0) if re.search(r'q\d+', str(q_text).lower()) else ""
            def_idx = next((i for i, h in enumerate(headers) if q_id and q_id in h.lower()), 0)
            with st.expander(f"❓ {str(q_text).strip()[:100]}", expanded=False):
                c1, c2 = st.columns([1, 2])
                mapping[q_text] = c1.selectbox(f"CSV Column:", headers, index=def_idx, key=f"m_{hash(q_text)}")
                auto_rej = [str(r).strip() for r in q_rows[q_rows[so_col].astype(str).str.contains("Disqualify", case=False, na=False)][a_col].tolist()] if so_col else []
                final_rules[q_text] = c2.multiselect("Reject if:", options, default=[r for r in auto_rej if r in options], key=f"r_{hash(q_text)}")

        # --- STEP 2: COMPARISON ---
        st.header("⚖️ Step 2: Profile vs. Screener Comparison")
        if 'consistency_pairs' not in st.session_state: st.session_state.consistency_pairs = 1
        consistency_rules = []
        for i in range(st.session_state.consistency_pairs):
            c1, c2 = st.columns(2)
            col_a = c1.selectbox(f"Pair {i+1}: Profile Data", ["None"] + headers, key=f"pa_{i}")
            col_b = c2.selectbox(f"Pair {i+1}: Screener Data", ["None"] + headers, key=f"pb_{i}")
            if col_a != "None" and col_b != "None": consistency_rules.append((col_a, col_b))
        if st.button("➕ Add Another Pair"):
            st.session_state.consistency_pairs += 1
            st.rerun()

        st.divider()

        # --- STEP 3: RUN ---
        if st.button("🚀 Run Full Audit"):
            tracker = {"api_saved": 0}
            
            def audit_row(row):
                # 1. Caution
                if 'Caution' in row and pd.notna(row['Caution']) and str(row['Caution']).strip():
                    tracker["api_saved"] += 1
                    return pd.Series(["Rejected", "Caution Note", "N/A"])
                # 2. Screener Logic
                for q, bads in final_rules.items():
                    if str(row.get(mapping[q])).strip() in bads:
                        tracker["api_saved"] += 1
                        return pd.Series(["Rejected", f"Failed: {q}", "N/A"])
                # 3. IPQS API
                if ipqs_key and actual_phone_col:
                    res = check_ipqs_phone(row[actual_phone_col], ipqs_key)
                    if res:
                        carrier = res.get('carrier', 'Unknown')
                        if reject_voip and res.get('voip'): return pd.Series(["Rejected", f"VOIP ({carrier})", carrier])
                        return pd.Series(["Qualified", "Pass", carrier])
                return pd.Series(["Qualified", "Pass", "Unknown"])

            with st.spinner("Analyzing integrity..."):
                df_audit = df_resp.copy()
                df_audit[['Status', 'Reason', 'Carrier']] = df_audit.apply(audit_row, axis=1)
                
                # Fuzzy Patterns
                q_cols = list(set(mapping.values()))
                df_audit['Pattern'] = df_audit[q_cols].astype(str).agg('-'.join, axis=1)
                fuzzy_groups, seen = [], set()
                for i in range(len(df_audit)):
                    if i in seen: continue
                    p_i = df_audit.iloc[i]['Pattern']
                    group = [i]
                    for j in range(i+1, len(df_audit)):
                        if fuzz.ratio(p_i, df_audit.iloc[j]['Pattern']) >= fuzzy_threshold:
                            group.append(j); seen.add(j)
                    if len(group) > 1: fuzzy_groups.append(group)

            st.header("📊 Audit Results")
            m1, m2, m3 = st.columns(3)
            m1.metric("✅ Qualified", (df_audit['Status'] == "Qualified").sum())
            m2.metric("❌ Rejected", (df_audit['Status'] == "Rejected").sum())
            m3.metric("💰 API Credits Saved", tracker["api_saved"])

            tabs = st.tabs(["🚩 Patterns", "⚖️ Mismatches", "🔍 Detailed Review", "📥 Export"])
            with tabs[0]:
                for g in fuzzy_groups:
                    with st.expander(f"🚩 Cluster: {len(g)} Suspects"):
                        st.table(df_audit.iloc[g][[p_id_col, 'Forename', 'Surname', 'Status']])
            with tabs[1]:
                for ca, cb in consistency_rules:
                    mismatches = df_audit[df_audit[ca].apply(normalize) != df_audit[cb].apply(normalize)]
                    if not mismatches.empty:
                        st.error(f"Mismatch: {ca} vs {cb}")
                        st.table(mismatches[[p_id_col, ca, cb, 'Status']])
            with tabs[2]:
                search = st.text_input("Search ID to Review:")
                r_list = df_audit[df_audit['Status'] == "Rejected"]
                if search: r_list = r_list[r_list[p_id_col].astype(str).str.contains(search)]
                for _, r in r_list.iterrows():
                    cl, cr = st.columns([4, 1])
                    cl.write(f"**{r[p_id_col]}**: {r['Forename']} {r['Surname']} ({r['Reason']})")
                    if actual_phone_col and cr.button("Check Phone", key=f"f_{r[p_id_col]}"): 
                        st.json(check_ipqs_phone(r[actual_phone_col], ipqs_key))
            with tabs[3]:
                st.dataframe(df_audit)
                st.download_button("📥 Download Final Report", df_audit.to_csv(index=False).encode('utf-8-sig'), "pfr_audit.csv")

    except Exception as e:
        st.error(f"Critical Error: {e}")
