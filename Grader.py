import streamlit as st
import pandas as pd
import requests
import re
from thefuzz import fuzz

# --- SECURITY GATE ---
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
    st.set_page_config(page_title="PFR Candidate Checker", layout="wide")
    st.title("🕵️ PFR Candidate Checker")

    def check_ipqs_phone(phone_number, api_key):H67E8mmH292LeSaTgbrufW5qzj68VEnG
        if not api_key: return None
        clean_num = re.sub(r'\D', '', str(phone_number))
        url = f"https://www.ipqualityscore.com/api/json/phone/validate/{api_key}/{clean_num}"
        try:
            res = requests.get(url, timeout=4).json()
            return res if res.get('success') else None
        except: return None

    def normalize(text):
        return re.sub(r'[^a-z0-9]', '', str(text).lower()).strip()

    # --- LOADERS ---
    col1, col2 = st.columns(2)
    with col1:
        resp_file = st.file_uploader("1. Upload Call List", type=["csv", "xlsx"])
    with col2:
        screen_file = st.file_uploader("2. Upload PFR Screener", type=["xlsx"])

    # --- SIDEBAR ---
    st.sidebar.header("⚙️ Logic Settings")
    ipqs_key = "H67E8mmH292LeSaTgbrufW5qzj68VEnG"
    reject_voip = st.sidebar.checkbox("Auto-Reject VOIP", value=True)
    fuzzy_threshold = st.sidebar.slider("Pattern Match %", 85, 100, 95)
    
    if resp_file and screen_file:
        df_resp = pd.read_csv(resp_file, encoding='latin1') if resp_file.name.endswith('.csv') else pd.read_excel(resp_file)
        df_resp.columns = [str(c).strip() for c in df_resp.columns]
        headers = df_resp.columns.tolist()
        p_id_col = next((c for c in ['Participant ID', 'ID'] if c in df_resp.columns), headers[0])

        # SCREENER PARSER
        raw_screen = pd.read_excel(screen_file, header=None)
        h_idx = next(i for i, row in raw_screen.iterrows() if str(row[0]).strip().lower() in ["question", "questions"])
        df_screen = pd.read_excel(screen_file, header=h_idx)
        df_screen.iloc[:, 0] = df_screen.iloc[:, 0].ffill()
        q_col, a_col = df_screen.columns[0], df_screen.columns[1]
        so_col = next((c for c in df_screen.columns if any(k in c.lower() for k in ["screen-out", "disqualify"])), None)
        logic_df = df_screen.dropna(subset=[a_col])

        # --- STEP 1: SCREENER MAPPING ---
        st.header("⚙️ Step 1: Screener Logic Mapping")
        final_rules, mapping = {}, {}
        for q_text in logic_df[q_col].unique():
            q_rows = logic_df[logic_df[q_col] == q_text]
            options = [str(o).strip() for o in q_rows[a_col].unique().tolist() if pd.notna(o)]
            q_id = re.search(r'q\d+', str(q_text).lower()).group(0) if re.search(r'q\d+', str(q_text).lower()) else ""
            def_idx = next((i for i, h in enumerate(headers) if q_id and q_id in h.lower()), 0)

            with st.expander(f"❓ {str(q_text).strip()[:100]}", expanded=False):
                c1, c2 = st.columns([1, 2])
                mapped_col = c1.selectbox(f"CSV Column:", headers, index=def_idx, key=f"map_{hash(q_text)}")
                mapping[q_text] = mapped_col
                auto_rej = [str(r).strip() for r in q_rows[q_rows[so_col].astype(str).str.contains("Disqualify", case=False, na=False)][a_col].tolist()] if so_col else []
                final_rules[q_text] = c2.multiselect("Reject if:", options, default=[r for r in auto_rej if r in options], key=f"rej_{hash(q_text)}")

        # --- STEP 2: COMPARISON COLUMNS ---
        st.header("⚖️ Step 2: Profile vs. Screener Comparison")
        if 'consistency_pairs' not in st.session_state: st.session_state.consistency_pairs = 1
        
        consistency_rules = []
        for i in range(st.session_state.consistency_pairs):
            c1, c2 = st.columns(2)
            col_a = c1.selectbox(f"Pair {i+1}: Profile Column", ["None"] + headers, key=f"pa_{i}")
            col_b = c2.selectbox(f"Pair {i+1}: Screener Column", ["None"] + headers, key=f"pb_{i}")
            if col_a != "None" and col_b != "None":
                consistency_rules.append((col_a, col_b))
        
        if st.button("➕ Add Another Comparison Pair"):
            st.session_state.consistency_pairs += 1
            st.rerun()

        st.divider()

        if st.button("🚀 Run Full Audit"):
            tracker = {"api_saved": 0}
            
            def audit(row):
                # 1. Caution Note (Automatic Rejection)
                if 'Caution' in row and pd.notna(row['Caution']) and str(row['Caution']).strip():
                    tracker["api_saved"] += 1
                    return pd.Series(["Rejected", "Caution Note", "N/A"])
                
                # 2. Screener Logic
                for q, bads in final_rules.items():
                    if str(row.get(mapping[q])).strip() in bads:
                        tracker["api_saved"] += 1
                        return pd.Series(["Rejected", f"Failed: {q}", "N/A"])
                
                # 3. IPQS Final Gate
                if ipqs_key and 'Mobile' in row:
                    res = check_ipqs_phone(row['Mobile'], ipqs_key)
                    if res:
                        if reject_voip and res.get('voip'): 
                            return pd.Series(["Rejected", f"VOIP ({res.get('carrier')})", res.get('carrier')])
                        return pd.Series(["Qualified", "Pass", res.get('carrier')])
                
                return pd.Series(["Qualified", "Pass", "Unknown"])

            df_resp[['Status', 'Reason', 'Carrier']] = df_resp.apply(audit, axis=1)

            # Patterns & Duplicates Logic
            q_cols = list(set(mapping.values()))
            df_resp['Pattern'] = df_resp[q_cols].astype(str).agg('-'.join, axis=1)
            
            # --- RESULTS DASHBOARD ---
            st.header("📊 Results")
            m1, m2, m3 = st.columns(3)
            m1.metric("✅ Qualified", (df_resp['Status'] == "Qualified").sum())
            m2.metric("❌ Rejected", (df_resp['Status'] == "Rejected").sum())
            m3.metric("💰 API Credits Saved", tracker["api_saved"])

            t1, t2, t3, t4 = st.tabs(["👥 Patterns & Clusters", "⚖️ Comparison Review", "🔍 Force Check", "📥 Export"])
            
            with t1:
                # Answer Clusters
                for pat, group in df_resp[df_resp.duplicated('Pattern', keep=False)].groupby('Pattern'):
                    with st.expander(f"🚩 Identical Answer Group ({len(group)})"):
                        st.table(group[[p_id_col, 'Forename', 'Surname', 'Status']])
                # Phone Clusters
                if 'Mobile' in df_resp.columns:
                    df_resp['P_Clean'] = df_resp['Mobile'].astype(str).str.replace(r'\D', '', regex=True).str[:10]
                    for prefix, group in df_resp[df_resp.duplicated('P_Clean', keep=False)].groupby('P_Clean'):
                        with st.expander(f"🚩 Phone Cluster: {prefix}XXX ({len(group)})"):
                            st.table(group[[p_id_col, 'Forename', 'Surname', 'Status']])

            with t2:
                st.subheader("Profile vs Screener Mismatches")
                mismatch_found = False
                for ca, cb in consistency_rules:
                    mismatches = df_resp[df_resp[ca].apply(normalize) != df_resp[cb].apply(normalize)]
                    if not mismatches.empty:
                        mismatch_found = True
                        with st.expander(f"🚩 Mismatch: {ca} vs {cb} ({len(mismatches)})"):
                            st.table(mismatches[[p_id_col, 'Forename', 'Surname', ca, cb, 'Status']])
                if not mismatch_found:
                    st.success("No consistency mismatches found!")

            with t3:
                search = st.text_input("Search ID to Review:")
                r_list = df_resp[df_resp['Status'] == "Rejected"]
                if search: r_list = r_list[r_list[p_id_col].astype(str).str.contains(search)]
                for _, r in r_list.iterrows():
                    cl, cr = st.columns([4, 1])
                    cl.write(f"**{r[p_id_col]}**: {r['Forename']} {r['Surname']} ({r['Reason']})")
                    if cr.button("Check Phone", key=f"f_{r[p_id_col]}"): st.json(check_ipqs_phone(r['Mobile'], ipqs_key))

            with t4:
                st.dataframe(df_resp)
                st.download_button("Download Report", df_resp.to_csv(index=False).encode('utf-8-sig'), "pfr_candidate_audit.csv")

    except Exception as e: st.error(f"Error: {e}")
