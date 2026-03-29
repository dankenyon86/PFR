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

def login_callback():
    if st.session_state["pwd_input"] == "BruceWillis":
        st.session_state["password_correct"] = True
    else:
        st.error("❌ Incorrect password.")

if not st.session_state["password_correct"]:
    st.title("🔐 PFR Internal Security")
    st.text_input("Enter PFR Access Code:", type="password", key="pwd_input", on_change=login_callback)
    if st.button("Unlock Checker"):
        login_callback()
    st.stop()

# --- 3. APP START ---
st.title("🕵️ PFR Candidate Checker")

# --- 4. SMART WORLDWIDE API FUNCTION ---
def check_ipqs_phone(phone_number, api_key, default_country_iso, default_dial_code):
    if not api_key or pd.isna(phone_number): 
        return None
    raw_val = str(phone_number).strip()
    clean_num = re.sub(r'[^0-9+]', '', raw_val)
    if not clean_num.startswith('+'):
        if clean_num.startswith('0'):
            clean_num = clean_num[1:]
        clean_num = default_dial_code + clean_num
    else:
        clean_num = clean_num.replace('+', '')
    url = f"https://www.ipqualityscore.com/api/json/phone/{api_key}/{clean_num}"
    try:
        res = requests.get(url, params={'country': default_country_iso}, timeout=5)
        data = res.json()
        return data if data.get('success') else None
    except: return None

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

country_map = {
    "United Kingdom (44)": ("GB", "44"),
    "USA / Canada (1)": ("US", "1"),
    "Australia (61)": ("AU", "61"),
    "Germany (49)": ("DE", "49"),
    "Ireland (353)": ("IE", "353")
}
country_label = st.sidebar.selectbox("Default Country (if no + provided)", list(country_map.keys()))
iso_code, dial_code = country_map[country_label]

reject_voip = st.sidebar.checkbox("Auto-Reject VOIP", value=True)
fuzzy_threshold = st.sidebar.slider("Fuzzy Pattern Sensitivity %", 80, 100, 95)

if st.sidebar.button("Logout"):
    st.session_state["password_correct"] = False
    st.rerun()

# --- 7. MAIN LOGIC ---
if resp_file and screen_file:
    try:
        # Load Data + BOM Fix (Fixes the ï»¿ issue)
        if resp_file.name.endswith('.csv'):
            df_resp = pd.read_csv(resp_file, encoding='utf-8-sig')
        else:
            df_resp = pd.read_excel(resp_file)
        
        # Clean Headers
        df_resp.columns = [str(c).replace('\ufeff', '').replace('ï»¿', '').strip() for c in df_resp.columns]
        headers = df_resp.columns.tolist()
        norm_headers = [normalize(h) for h in headers]
        p_id_col = next((c for c in ['Participant ID', 'ID', 'Ref'] if c in headers), headers[0])

        # Broad Phone Finder
        actual_phone_col = next((headers[i] for i, nh in enumerate(norm_headers) if any(p in nh for p in ['mob', 'tel', 'phone'])), None)
        if actual_phone_col: st.sidebar.success(f"📱 API Linked: {actual_phone_col}")

        # Screener Logic
        raw_screen = pd.read_excel(screen_file, header=None)
        h_idx = next(i for i, row in raw_screen.iterrows() if str(row[0]).strip().lower() in ["question", "questions"])
        df_screen = pd.read_excel(screen_file, header=h_idx)
        df_screen.iloc[:, 0] = df_screen.iloc[:, 0].ffill()
        q_col, a_col = df_screen.columns[0], df_screen.columns[1]
        so_col = next((c for c in df_screen.columns if any(k in str(c).lower() for k in ["screen-out", "disqualify"])), None)
        logic_df = df_screen.dropna(subset=[a_col])

        st.header("⚙️ Step 1: Screener Logic Mapping")
        final_rules, mapping = {}, {}
        for q_text in logic_df[q_col].unique():
            q_rows = logic_df[logic_df[q_col] == q_text]
            options = [str(o).strip() for o in q_rows[a_col].unique().tolist() if pd.notna(o)]
            
            # --- RESTORED SMART MATCHING LOGIC ---
            q_id_match = re.search(r'q\d+', str(q_text).lower())
            q_id = q_id_match.group(0) if q_id_match else None
            norm_q_text = normalize(q_text)[:20] 
            
            def_idx = 0
            for i, nh in enumerate(norm_headers):
                if q_id and q_id in nh:
                    def_idx = i
                    break
                elif norm_q_text in nh:
                    def_idx = i
                    break

            with st.expander(f"❓ {str(q_text).strip()[:100]}", expanded=False):
                c1, c2 = st.columns([1, 2])
                mapping[q_text] = c1.selectbox(f"CSV Col:", headers, index=def_idx, key=f"m_{hash(q_text)}")
                auto_rej = [str(r).strip() for r in q_rows[q_rows[so_col].astype(str).str.contains("Disqualify", case=False, na=False)][a_col].tolist()] if so_col else []
                final_rules[q_text] = c2.multiselect("Reject if:", options, default=[r for r in auto_rej if r in options], key=f"r_{hash(q_text)}")

        st.header("⚖️ Step 2: Comparison Pairs")
        if 'consistency_pairs' not in st.session_state: st.session_state.consistency_pairs = 1
        consistency_rules = []
        for i in range(st.session_state.consistency_pairs):
            c1, c2 = st.columns(2)
            col_a = c1.selectbox(f"Profile Col", ["None"] + headers, key=f"pa_{i}")
            col_b = c2.selectbox(f"Screener Col", ["None"] + headers, key=f"pb_{i}")
            if col_a != "None" and col_b != "None": consistency_rules.append((col_a, col_b))
        if st.button("➕ Add Another Pair"):
            st.session_state.consistency_pairs += 1
            st.rerun()

        if st.button("🚀 Run Full Audit"):
            tracker = {"api_saved": 0}
            def audit_row(row):
                if 'Caution' in row and pd.notna(row['Caution']) and str(row['Caution']).strip():
                    tracker["api_saved"] += 1
                    return pd.Series(["Rejected", "Caution Note", "N/A"])
                for q, bads in final_rules.items():
                    if str(row.get(mapping[q])).strip() in bads:
                        tracker["api_saved"] += 1
                        return pd.Series(["Rejected", f"Failed: {q}", "N/A"])
                if ipqs_key and actual_phone_col:
                    res = check_ipqs_phone(row[actual_phone_col], ipqs_key, iso_code, dial_code)
                    if res:
                        carrier = res.get('carrier', 'Valid')
                        if reject_voip and res.get('voip'): return pd.Series(["Rejected", f"VOIP ({carrier})", carrier])
                        return pd.Series(["Qualified", "Pass", carrier])
                return pd.Series(["Qualified", "Pass", "Unknown"])

            with st.spinner("Analyzing..."):
                # Use a fresh dataframe for results to avoid recursion issues
                df_results = df_resp.copy()
                df_results[['Status', 'Reason', 'Carrier']] = df_results.apply(audit_row, axis=1)
                
                q_cols = list(set(mapping.values()))
                df_results['Pattern'] = df_results[q_cols].astype(str).agg('-'.join, axis=1)
                fuzzy_groups, seen = [], set()
                for i in range(len(df_results)):
                    if i in seen: continue
                    p_i = df_results.iloc[i]['Pattern']
                    group = [i]
                    for j in range(i+1, len(df_results)):
                        if fuzz.ratio(p_i, df_results.iloc[j]['Pattern']) >= fuzzy_threshold:
                            group.append(j); seen.add(j)
                    if len(group) > 1: fuzzy_groups.append(group)

            st.header("📊 Results")
            m1, m2, m3 = st.columns(3)
            m1.metric("✅ Qualified", (df_results['Status'] == "Qualified").sum())
            m2.metric("❌ Rejected", (df_results['Status'] == "Rejected").sum())
            m3.metric("💰 API Saved", tracker["api_saved"])

            t1, t2, t3, t4 = st.tabs(["🚩 Patterns", "⚖️ Mismatches", "🔍 Detailed Review", "📥 Export"])
            with t1:
                for g in fuzzy_groups:
                    with st.expander(f"🚩 Cluster ({len(g)} Candidates)"):
                        st.table(df_results.iloc[g][[p_id_col, 'Forename', 'Surname', 'Status']])
            with t2:
                for ca, cb in consistency_rules:
                    mismatches = df_results[df_results[ca].astype(str).apply(normalize) != df_results[cb].astype(str).apply(normalize)]
                    if not mismatches.empty:
                        st.error(f"Mismatch: {ca} vs {cb}")
                        st.table(mismatches[[p_id_col, ca, cb]])
            with t3:
                search = st.text_input("Search ID to Force Check:")
                r_list = df_results[df_results['Status'] == "Rejected"]
                if search: r_list = r_list[r_list[p_id_col].astype(str).str.contains(search)]
                for _, r in r_list.iterrows():
                    cl, cr = st.columns([4, 1])
                    cl.write(f"**{r[p_id_col]}**: {r['Forename']} {r['Surname']} ({r['Reason']})")
                    if actual_phone_col and cr.button("Check Phone", key=f"f_{r[p_id_col]}"):
                        st.json(check_ipqs_phone(r[actual_phone_col], ipqs_key, iso_code, dial_code))
            with t4:
                st.dataframe(df_results)
                st.download_button("Download Audit", df_results.to_csv(index=False).encode('utf-8-sig'), "pfr_audit.csv")

    except Exception as e: st.error(f"Error: {e}")
