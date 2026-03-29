import streamlit as st
import pandas as pd
import requests
import re
from thefuzz import fuzz

# --- INITIAL SETUP ---
st.set_page_config(page_title="PFR Fraud Detective Pro", layout="wide")
st.title("🕵️ PFR Fraud Detective (Optimized)")

# --- HELPER FUNCTIONS ---
def normalize(text):
    return re.sub(r'[^a-z0-9]', '', str(text).lower()).strip()

def check_ipqs_phone(phone_number, api_key):
    clean_num = re.sub(r'\D', '', str(phone_number))
    url = f"https://www.ipqualityscore.com/api/json/phone/validate/{api_key}/{clean_num}"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        return data if data.get('success') else None
    except: return None

# --- STEP 1: LOADERS ---
col1, col2 = st.columns(2)
with col1:
    resp_file = st.file_uploader("1. Upload Call List", type=["csv", "xlsx"])
with col2:
    screen_file = st.file_uploader("2. Upload PFR Screener", type=["xlsx"])

# --- SIDEBAR ---
st.sidebar.header("🔑 API & Logic")
ipqs_key = st.sidebar.text_input("IPQS API Key", type="password")
reject_voip = st.sidebar.checkbox("Auto-Reject VOIP", value=True)
phone_match_len = st.sidebar.slider("Phone Match Length", 5, 11, 10)
fuzzy_threshold = st.sidebar.slider("Pattern Match %", 80, 100, 95)

if resp_file and screen_file:
    try:
        df_resp = pd.read_csv(resp_file, encoding='latin1') if resp_file.name.endswith('.csv') else pd.read_excel(resp_file)
        df_resp.columns = [str(c).strip() for c in df_resp.columns]
        call_list_headers = df_resp.columns.tolist()
        p_id_col = 'Participant ID' if 'Participant ID' in df_resp.columns else call_list_headers[0]

        # Parser
        raw_screen = pd.read_excel(screen_file, header=None)
        h_idx = next(i for i, row in raw_screen.iterrows() if str(row[0]).strip().lower() in ["question", "questions"])
        df_screen = pd.read_excel(screen_file, header=h_idx)
        df_screen.iloc[:, 0] = df_screen.iloc[:, 0].ffill()
        q_col, a_col = df_screen.columns[0], df_screen.columns[1]
        so_col = next((c for c in df_screen.columns if any(k in c.lower() for k in ["screen-out", "disqualify"])), None)
        logic_df = df_screen.dropna(subset=[a_col])

        # Mapping Logic
        st.header("⚙️ Step 1: Mapping")
        final_rules, mapping = {}, {}
        for q_text in logic_df[q_col].unique():
            q_rows = logic_df[logic_df[q_col] == q_text]
            options = [str(o).strip() for o in q_rows[a_col].unique().tolist() if pd.notna(o)]
            auto_rej = [str(r).strip() for r in q_rows[q_rows[so_col].astype(str).str.contains("Disqualify", case=False, na=False)][a_col].tolist() if str(r).strip() in options] if so_col else []
            with st.expander(f"📂 {str(q_text).strip()[:100]}"):
                c_l, c_r = st.columns([1, 2])
                q_id = re.search(r'q\d+', str(q_text).lower()).group(0) if re.search(r'q\d+', str(q_text).lower()) else normalize(q_text)[:5]
                mapping[q_text] = c_l.selectbox("Link CSV:", call_list_headers, index=next((i for i, h in enumerate(call_list_headers) if q_id in normalize(h)), 0), key=f"m_{hash(q_text)}")
                final_rules[q_text] = [str(r).lower().strip() for r in c_r.multiselect("Reject if:", options, default=auto_rej, key=f"r_{hash(q_text)}")]

        if st.button("🚀 Run Optimized Audit"):
            # FIXED: We use a list to track saved count to avoid the 'nonlocal' syntax error
            tracker = {"api_saved": 0}

            def audit(row):
                if 'Caution' in row and pd.notna(row['Caution']) and str(row['Caution']).strip() != "":
                    tracker["api_saved"] += 1
                    return pd.Series(["Rejected", "Caution Note", "N/A"])
                for q, bads in final_rules.items():
                    if str(row.get(mapping[q])).strip().lower() in bads:
                        tracker["api_saved"] += 1
                        return pd.Series(["Rejected", f"Failed: {q}", "N/A"])
                if ipqs_key and 'Mobile' in row:
                    res = check_ipqs_phone(row['Mobile'], ipqs_key)
                    if res:
                        if reject_voip and res.get('voip'): return pd.Series(["Rejected", f"VOIP ({res.get('carrier')})", res.get('carrier')])
                        return pd.Series(["Qualified", "Pass", res.get('carrier')])
                return pd.Series(["Qualified", "Pass", "Unknown"])

            df_resp[['Status', 'Reason', 'Carrier']] = df_resp.apply(audit, axis=1)

            # Fuzzy Matcher
            q_cols = list(set(mapping.values()))
            df_resp['Pattern'] = df_resp[q_cols].astype(str).agg(' '.join, axis=1)
            fuzzy_groups, seen = [], set()
            for i in range(len(df_resp)):
                if i not in seen:
                    cluster = [j for j in range(i+1, len(df_resp)) if fuzz.ratio(df_resp.iloc[i]['Pattern'], df_resp.iloc[j]['Pattern']) >= fuzzy_threshold]
                    if cluster: 
                        cluster.insert(0, i)
                        fuzzy_groups.append(cluster)
                        seen.update(cluster)

            # Results Display
            st.header("📊 Results")
            c1, c2, c3 = st.columns(3)
            c1.metric("✅ Qualified", (df_resp['Status'] == "Qualified").sum())
            c2.metric("❌ Rejected", (df_resp['Status'] == "Rejected").sum())
            c3.metric("💰 API Credits Saved", tracker["api_saved"])

            t1, t2, t3, t4 = st.tabs(["👥 Patterns", "📱 Clusters", "🔍 Force Check", "📥 Export"])
            
            with t1:
                for g in fuzzy_groups:
                    with st.expander(f"🚩 Near-Identical Group ({len(g)})"): st.table(df_resp.iloc[g][[p_id_col, 'Forename', 'Surname', 'Status']])
            
            with t2:
                if 'Mobile' in df_resp.columns:
                    df_resp['P_Clean'] = df_resp['Mobile'].astype(str).str.replace(r'\D', '', regex=True).str[:phone_match_len]
                    for prefix, group in df_resp[df_resp.duplicated('P_Clean', keep=False)].groupby('P_Clean'):
                        with st.expander(f"🚩 Cluster: {prefix}XXXX"): st.table(group[[p_id_col, 'Forename', 'Surname', 'Mobile']])
            
            with t3:
                for _, r in df_resp[df_resp['Status'] == "Rejected"].iterrows():
                    cl, cr = st.columns([4, 1])
                    cl.write(f"**{r[p_id_col]}**: {r['Forename']} {r['Surname']} ({r['Reason']})")
                    if cr.button("Check Phone", key=f"f_{r[p_id_col]}"): st.write(check_ipqs_phone(r['Mobile'], ipqs_key))
            
            with t4:
                # Add a filter before downloading
                filter_choice = st.radio("Download Scope:", ["All Records", "Qualified Only", "Rejected Only"], horizontal=True)
                df_out = df_resp.copy()
                if filter_choice == "Qualified Only": df_out = df_resp[df_resp['Status'] == "Qualified"]
                elif filter_choice == "Rejected Only": df_out = df_resp[df_resp['Status'] == "Rejected"]
                
                st.dataframe(df_out)
                st.download_button("📥 Download Filtered CSV", df_out.to_csv(index=False).encode('utf-8-sig'), "pfr_audit_report.csv")

    except Exception as e: st.error(f"Error: {e}")
