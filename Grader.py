import streamlit as st
import pandas as pd
import requests
import re
from thefuzz import fuzz

# --- 1. CONFIG (MUST BE FIRST) ---
st.set_page_config(page_title="PFR Candidate Checker", layout="wide")

# --- 2. SECURITY GATE ---
def check_password():
    if "password_correct" not in st.session_state:
        st.title("🔐 PFR Internal Security")
        pw = st.text_input("Enter PFR Access Code:", type="password")
        if pw == "BruceWillis":
            st.session_state["password_correct"] = True
            st.rerun()
        elif pw:
            st.error("Incorrect password.")
        return False
    return True

if check_password():
    st.title("🕵️ PFR Candidate Checker")

    # --- 3. HELPER FUNCTIONS ---
    def check_ipqs_phone(phone_number, api_key):
        if not api_key: 
            return None
        clean_num = re.sub(r'\D', '', str(phone_number))
        url = f"https://www.ipqualityscore.com/api/json/phone/validate/{api_key}/{clean_num}"
        try:
            res = requests.get(url, timeout=4).json()
            return res if res.get('success') else None
        except: 
            return None

    def normalize(text):
        return re.sub(r'[^a-z0-9]', '', str(text).lower()).strip()

    # --- 4. LOADERS ---
    col1, col2 = st.columns(2)
    with col1:
        resp_file = st.file_uploader("1. Upload Call List (Data)", type=["csv", "xlsx"])
    with col2:
        screen_file = st.file_uploader("2. Upload PFR Screener (Logic)", type=["xlsx"])

    # --- 5. SIDEBAR SETTINGS ---
    st.sidebar.header("⚙️ Logic Settings")
    ipqs_key = "H67E8mmH292LeSaTgbrufW5qzj68VEnG" 
    reject_voip = st.sidebar.checkbox("Auto-Reject VOIP", value=True)
    fuzzy_threshold = st.sidebar.slider("Fuzzy Match Sensitivity %", 80, 100, 95)
    
    if resp_file and screen_file:
        try:
            # Load Data
            if resp_file.name.endswith('.csv'):
                df_resp = pd.read_csv(resp_file, encoding='latin1')
            else:
                df_resp = pd.read_excel(resp_file)
            
            df_resp.columns = [str(c).strip() for c in df_resp.columns]
            headers = df_resp.columns.tolist()
            p_id_col = next((c for c in ['Participant ID', 'ID', 'Ref'] if c in df_resp.columns), headers[0])

            # Load Screener Logic
            raw_screen = pd.read_excel(screen_file, header=None)
            h_idx = next(i for i, row in raw_screen.iterrows() if str(row[0]).strip().lower() in ["question", "questions"])
            df_screen = pd.read_excel(screen_file, header=h_idx)
            df_screen.iloc[:, 0] = df_screen.iloc[:, 0].ffill()
            
            q_col, a_col = df_screen.columns[0], df_screen.columns[1]
            so_col = next((c for c in df_screen.columns if any(k in str(c).lower() for k in ["screen-out", "disqualify"])), None)
            logic_df = df_screen.dropna(subset=[a_col])

            # --- STEP 1: SCREENER MAPPING ---
            st.header("⚙️ Step 1: Screener Logic Mapping")
            final_rules, mapping = {}, {}
            
            for q_text in logic_df[q_col].unique():
                q_rows = logic_df[logic_df[q_col] == q_text]
                options = [str(o).strip() for o in q_rows[a_col].unique().tolist() if pd.notna(o)]
                
                q_id_match = re.search(r'q\d+', str(q_text).lower())
                q_id = q_id_match.group(0) if q_id_match else None
                
                def_idx = 0 
                for i, h in enumerate(headers):
                    clean_h = str(h).lower().strip()
                    if q_id and q_id in clean_h:
                        def_idx = i
                        break
                    elif str(q_text).lower()[:15] in clean_h:
                        def_idx = i
                        break

                with st.expander(f"❓ {str(q_text).strip()[:100]}", expanded=False):
                    c1, c2 = st.columns([1, 2])
                    mapped_col = c1.selectbox(f"CSV Column:", headers, index=def_idx, key=f"map_{hash(q_text)}")
                    mapping[q_text] = mapped_col
                    
                    auto_rej = []
                    if so_col:
                        auto_rej = [str(r).strip() for r in q_rows[q_rows[so_col].astype(str).str.contains("Disqualify", case=False, na=False)][a_col].tolist()]
                    
                    final_rules[q_text] = c2.multiselect("Reject if:", options, default=[r for r in auto_rej if r in options], key=f"rej_{hash(q_text)}")

            # --- STEP 2: PROFILE VS SCREENER COMPARISON ---
            st.header("⚖️ Step 2: Profile vs. Screener Comparison")
            if 'consistency_pairs' not in st.session_state: 
                st.session_state.consistency_pairs = 1
            
            consistency_rules = []
            for i in range(st.session_state.consistency_pairs):
                c1, c2 = st.columns(2)
                col_a = c1.selectbox(f"Pair {i+1}: Profile Data", ["None"] + headers, key=f"pa_{i}")
                col_b = c2.selectbox(f"Pair {i+1}: Screener Data", ["None"] + headers, key=f"pb_{i}")
                if col_a != "None" and col_b != "None":
                    consistency_rules.append((col_a, col_b))
            
            if st.button("➕ Add Another Comparison Pair"):
                st.session_state.consistency_pairs += 1
                st.rerun()

            st.divider()

            # --- STEP 3: EXECUTION ---
            if st.button("🚀 Run Full Audit"):
                tracker = {"api_saved": 0}

                def audit(row):
                    # 1. Check Caution Column
                    if 'Caution' in row and pd.notna(row['Caution']) and str(row['Caution']).strip():
                        tracker["api_saved"] += 1
                        return pd.Series(["Rejected", "Caution Note", "N/A"])
                    
                    # 2. Check Screener Logic
                    for q, bad_options in final_rules.items():
                        user_answer = str(row.get(mapping[q])).strip()
                        if user_answer in bad_options:
                            tracker["api_saved"] += 1
                            return pd.Series(["Rejected", f"Failed: {q}", "N/A"])
                    
                    # 3. IPQS Phone Gate
                    phone_val = row.get('Mobile') or row.get('Phone')
                    if ipqs_key and pd.notna(phone_val):
                        res = check_ipqs_phone(phone_val, ipqs_key)
                        if res:
                            carrier = res.get('carrier', 'Unknown')
                            if reject_voip and res.get('voip'): 
                                return pd.Series(["Rejected", f"VOIP ({carrier})", carrier])
                            return pd.Series(["Qualified", "Pass", carrier])
                    
                    return pd.Series(["Qualified", "Pass", "Unknown"])

                with st.spinner("Analyzing candidate integrity..."):
                    df_audit = df_resp.copy()
                    df_audit[['Status', 'Reason', 'Carrier']] = df_audit.apply(audit, axis=1)

                    # --- FUZZY PATTERN LOGIC ---
                    q_cols = list(set(mapping.values()))
                    df_audit['Pattern'] = df_audit[q_cols].astype(str).agg('-'.join, axis=1)
                    
                    fuzzy_groups, seen = [], set()
                    for i in range(len(df_audit)):
                        if i in seen: continue
                        p_i = df_audit.iloc[i]['Pattern']
                        group = [i]
                        for j in range(i+1, len(df_audit)):
                            if fuzz.ratio(p_i, df_audit.iloc[j]['Pattern']) >= fuzzy_threshold:
                                group.append(j)
                                seen.add(j)
                        if len(group) > 1:
                            fuzzy_groups.append(group)

                # --- DASHBOARD ---
                st.header("📊 Audit Results")
                m1, m2, m3 = st.columns(3)
                m1.metric("✅ Qualified", (df_audit['Status'] == "Qualified").sum())
                m2.metric("❌ Rejected", (df_audit['Status'] == "Rejected").sum())
                m3.metric("💰 API Credits Saved", tracker["api_saved"])

                tabs = st.tabs(["🚩 Patterns", "⚖️ Mismatches", "🔍 Detailed Review", "📥 Export"])
                
                with tabs[0]:
                    st.subheader(f"Near-Identical Answer Clusters ({fuzzy_threshold}% Match)")
                    if fuzzy_groups:
                        for g in fuzzy_groups:
                            with st.expander(f"🚩 Cluster: {len(g)} Suspect Candidates"):
                                st.table(df_audit.iloc[g][[p_id_col, 'Forename', 'Surname', 'Status']])
                    else:
                        st.success("No suspicious duplicate patterns found.")

                with tabs[1]:
                    st.subheader("Consistency Checks")
                    mfound = False
                    for ca, cb in consistency_rules:
                        mismatches = df_audit[df_audit[ca].apply(normalize) != df_audit[cb].apply(normalize)]
                        if not mismatches.empty:
                            mfound = True
                            st.error(f"Mismatch found between {ca} and {cb}")
                            st.table(mismatches[[p_id_col, ca, cb, 'Status']])
                    if not mfound: st.success("All comparison pairs match perfectly.")

                with tabs[2]:
                    search = st.text_input("Search ID to Review:")
                    review_list = df_audit[df_audit['Status'] == "Rejected"]
                    if search:
                        review_list = review_list[review_list[p_id_col].astype(str).str.contains(search)]
                    
                    for _, r in review_list.iterrows():
                        cl, cr = st.columns([4, 1])
                        cl.write(f"**{r[p_id_col]}**: {r['Forename']} {r['Surname']} ({r['Reason']})")
                        if cr.button("Check Phone", key=f"f_{r[p_id_col]}"): 
                            st.json(check_ipqs_phone(r['Mobile'], ipqs_key))

                with tabs[3]:
                    st.dataframe(df_audit)
                    csv = df_audit.to_csv(index=False).encode('utf-8-sig')
                    st.download_button("📥 Download Final Report", csv, "pfr_audit_report.csv", "text/csv")

        except Exception as e:
            st.error(f"Critical Error: {e}")

    # --- FOOTER ---
    if "password_correct" in st.session_state:
        st.sidebar.write("---")
        if st.sidebar.button("Logout"):
            del st.session_state["password_correct"]
            st.rerun()
