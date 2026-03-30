import streamlit as st
import pandas as pd
import requests
import re
from thefuzz import fuzz

st.set_page_config(page_title="PFR Candidate Checker", layout="wide")
st.title("🕵️ PFR Candidate Checker")

def check_ipqs_phone(phone_number, api_key, iso, dial):
    if not api_key or pd.isna(phone_number): return None
    clean_num = re.sub(r'[^0-9+]', '', str(phone_number).strip())
    if not clean_num.startswith('+'):
        if clean_num.startswith('0'): clean_num = clean_num[1:]
        clean_num = dial + clean_num
    else: 
        clean_num = clean_num.replace('+', '')
    
    url = f"https://www.ipqualityscore.com/api/json/phone/{api_key}/{clean_num}"
    try:
        res = requests.get(url, params={'country': iso, 'strictness': 1}, timeout=5)
        return res.json() if res.json().get('success') else None
    except: 
        return None

def normalize(text):
    return re.sub(r'[^a-z0-9]', '', str(text).lower()).strip()

col1, col2 = st.columns(2)
with col1:
    resp_file = st.file_uploader("1. Upload Call List (Data)", type=["csv", "xlsx"])
with col2:
    screen_file = st.file_uploader("2. Upload PFR Screener (Logic)", type=["xlsx"])

st.sidebar.header("⚙️ Risk & Logic Settings")
ipqs_key = "H67E8mmH292LeSaTgbrufW5qzj68VEnG1" 

country_map = {
    "United Kingdom (44)": ("GB", "44"), 
    "USA (1)": ("US", "1"), 
    "Australia (61)": ("AU", "61")
}
c_label = st.sidebar.selectbox("Default Country", list(country_map.keys()))
iso_code, dial_code = country_map[c_label]

st.sidebar.subheader("Fraud Weighting")
weight_mismatch = st.sidebar.slider("Mismatch Penalty (Step 2)", 0, 50, 30)
weight_pattern = st.sidebar.slider("Identical Pattern Penalty", 0, 50, 40)
fuzzy_threshold = st.sidebar.slider("Fuzzy Pattern Sensitivity %", 80, 100, 95)
reject_voip = st.sidebar.checkbox("Auto-Reject VOIP", value=True)

# ---  MAIN LOGIC ---
if resp_file and screen_file:
    try:
        if resp_file.name.endswith('.csv'):
            df_resp = pd.read_csv(resp_file, encoding='utf-8-sig')
        else:
            df_resp = pd.read_excel(resp_file)
        
        df_resp.columns = [str(c).replace('\ufeff', '').replace('ï»¿', '').strip() for c in df_resp.columns]
        headers = df_resp.columns.tolist()
        norm_headers = [normalize(h) for h in headers]
        
        p_id_col = next((c for c in ['Participant ID', 'ID', 'Ref'] if c in headers), headers[0])
        phone_col = next((headers[i] for i, nh in enumerate(norm_headers) if any(p in nh for p in ['mob', 'tel', 'phone'])), None)
        
        if phone_col: 
            st.sidebar.success(f"📱 API Linked: {phone_col}")

        raw_screen = pd.read_excel(screen_file, header=None)
        h_idx = next(i for i, row in raw_screen.iterrows() if str(row[0]).strip().lower() in ["question", "questions"])
        df_screen = pd.read_excel(screen_file, header=h_idx)
        df_screen.iloc[:, 0] = df_screen.iloc[:, 0].ffill()
        q_col, a_col = df_screen.columns[0], df_screen.columns[1]
        so_col = next((c for c in df_screen.columns if any(k in str(c).lower() for k in ["screen-out", "disqualify"])), None)
        logic_df = df_screen.dropna(subset=[a_col])

        st.header("⚙️ Step 1: Mapping")
        final_rules, mapping = {}, {}
        for q_text in logic_df[q_col].unique():
            q_rows = logic_df[logic_df[q_col] == q_text]
            options = [str(o).strip() for o in q_rows[a_col].unique().tolist() if pd.notna(o)]
            
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

        st.header("⚖️ Step 2: Comparison")
        if 'consistency_pairs' not in st.session_state: 
            st.session_state.consistency_pairs = 1
        
        consistency_rules = []
        for i in range(st.session_state.consistency_pairs):
            c1, c2 = st.columns(2)
            col_a = c1.selectbox(f"Profile Col", ["None"] + headers, key=f"pa_{i}")
            col_b = c2.selectbox(f"Screener Col", ["None"] + headers, key=f"pb_{i}")
            if col_a != "None" and col_b != "None": 
                consistency_rules.append((col_a, col_b))
        
        if st.button("➕ Add Pair"):
            st.session_state.consistency_pairs += 1
            st.rerun()

        if st.button("🚀 Run Full Audit"):
            result_cols = ['Status', 'Reason', 'Carrier', 'Risk %', 'Pattern']
            df_resp = df_resp.drop(columns=[c for c in result_cols if c in df_resp.columns])
            q_map_cols = list(set(mapping.values()))
            df_resp['Pattern'] = df_resp[q_map_cols].astype(str).agg('-'.join, axis=1)
            pattern_counts = df_resp['Pattern'].value_counts()

            def audit_row(row):
                behav_score = 0
                if pattern_counts[row['Pattern']] > 2: 
                    behav_score += weight_pattern
                for ca, cb in consistency_rules:
                    if normalize(row.get(ca)) != normalize(row.get(cb)): 
                        behav_score += weight_mismatch
                
                if 'Caution' in row and pd.notna(row['Caution']) and str(row['Caution']).strip():
                    return pd.Series(["Rejected", "Caution Note Flag", "N/A", 100])
                
                for q, bads in final_rules.items():
                    if str(row.get(mapping[q])).strip() in bads:
                        return pd.Series(["Rejected", f"Screener Fail: {q[:30]}...", "N/A", behav_score])

                api_score, carrier = 0, "N/A"
                if ipqs_key and phone_col:
                    res = check_ipqs_phone(row[phone_col], ipqs_key, iso_code, dial_code)
                    if res:
                        api_score = res.get('fraud_score', 0)
                        carrier = res.get('network', res.get('carrier', 'Valid'))
                        if reject_voip and res.get('voip'): 
                            return pd.Series(["Rejected", f"VOIP ({carrier})", carrier, 100])

                total = min(100, behav_score + api_score)
                status = "Rejected" if total > 70 else "Qualified"
                reason = "Pass" if status == "Qualified" else "High Risk Pattern"
                return pd.Series([status, reason, carrier, total])

            with st.spinner("Analyzing data integrity..."):
                df_resp[['Status', 'Reason', 'Carrier', 'Risk %']] = df_resp.apply(audit_row, axis=1)
                
                fuzzy_groups, seen = [], set()
                for i in range(len(df_resp)):
                    if i in seen: continue
                    p_i = df_resp.iloc[i]['Pattern']
                    group = [i]
                    for j in range(i+1, len(df_resp)):
                        if fuzz.ratio(p_i, df_resp.iloc[j]['Pattern']) >= fuzzy_threshold:
                            group.append(j); seen.add(j)
                    if len(group) > 1: fuzzy_groups.append(group)

            st.header("📊 Results")
            
            view_choice = st.radio("View Results:", ["All Candidates", "Qualified Only", "Rejected Only"], horizontal=True)
            
            filtered_df = df_resp.copy()
            if view_choice == "Qualified Only":
                filtered_df = filtered_df[filtered_df['Status'] == "Qualified"]
            elif view_choice == "Rejected Only":
                filtered_df = filtered_df[filtered_df['Status'] == "Rejected"]

            t1, t2, t3, t4 = st.tabs(["🚩 Bot Patterns", "⚖️ Step 2 Mismatches", "🔍 Detailed View", "📥 Export"])
            
            with t1:
                st.subheader("Near-Identical Answer Groups")
                for g in fuzzy_groups:
                    with st.expander(f"🚩 Cluster ({len(g)} Candidates)"):
                        st.table(df_resp.iloc[g][[p_id_col, 'Status', 'Risk %']])
            
            with t2:
                st.subheader("Step 2 Consistency Audit")
                for ca, cb in consistency_rules:
                    mm = df_resp[df_resp[ca].astype(str).apply(normalize) != df_resp[cb].astype(str).apply(normalize)]
                    if not mm.empty: 
                        st.error(f"Mismatch: {ca} vs {cb}")
                        st.table(mm[[p_id_col, ca, cb]])
            
            with t3:
                st.subheader(f"Detailed List: {view_choice}")
                st.dataframe(filtered_df[[p_id_col, 'Status', 'Reason', 'Risk %', 'Carrier'] + headers])
            
            with t4:
                st.subheader("Download Final Report")
                st.download_button("Download CSV", df_resp.to_csv(index=False).encode('utf-8-sig'), "pfr_audit_report.csv")

    except Exception as e: 
        st.error(f"Error during processing: {e}")
