import streamlit as st
import pandas as pd
import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from thefuzz import fuzz

# --- DEPENDENCY CHECK ---
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    st.error("Missing dependency: Please run 'pip install scikit-learn'")
    st.stop()

# --- 1. CONFIG ---
st.set_page_config(page_title="PFR Candidate Checker", layout="wide")

# --- 2. SIDEBAR LOGO & SETTINGS ---
try:
    st.sidebar.image("PFRLogo.png", use_container_width=True)
except:
    st.sidebar.info("💡 Note: Place 'PFRLogo.png' in the root folder to display logo.")

st.sidebar.header("⚙️ Risk & Logic Settings")
ipqs_key = "H67E8mmH292LeSaTgbrufW5qzj68VEnG" 

country_map = {"UK": ("GB", "44"), "US": ("US", "1"), "AU": ("AU", "61")}
c_label = st.sidebar.selectbox("Default Country", list(country_map.keys()))
iso, dial = country_map[c_label]

st.sidebar.subheader("Fraud Weighting")
weight_mismatch = st.sidebar.slider("Mismatch Penalty (Step 2)", 0, 50, 30)
weight_pattern = st.sidebar.slider("Identical Pattern Penalty", 0, 50, 40)
cluster_threshold = st.sidebar.slider("Bot Cluster Sensitivity", 0.7, 0.99, 0.9)
reject_voip = st.sidebar.checkbox("Auto-Reject VOIP", value=True)

# --- 3. HELPER FUNCTIONS ---
def normalize(x):
    return re.sub(r'[^a-z0-9]', '', str(x).lower()).strip()

def fetch_ipqs(phone, api_key, iso_code, dial_code):
    try:
        clean = re.sub(r'[^0-9+]', '', str(phone))
        if not clean.startswith('+'):
            if clean.startswith('0'): clean = clean[1:]
            clean = dial_code + clean
        else: clean = clean.replace('+', '')
        url = f"https://www.ipqualityscore.com/api/json/phone/{api_key}/{clean}"
        res = requests.get(url, params={'country': iso_code, 'strictness': 1}, timeout=5)
        data = res.json()
        if data.get("success"):
            return {"phone": phone, "fraud_score": data.get("fraud_score", 0), 
                    "carrier": data.get("carrier", "Unknown"), "voip": data.get("voip", False)}
    except: return None

def parallel_ipqs(phones, api_key, iso_code, dial_code):
    results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_ipqs, p, api_key, iso_code, dial_code): p for p in phones}
        for f in as_completed(futures):
            r = f.result()
            if r: results[r["phone"]] = r
    return results

def detect_clusters(patterns, threshold=0.9):
    vectorizer = TfidfVectorizer()
    X = vectorizer.fit_transform(patterns)
    sim_matrix = cosine_similarity(X)
    groups = []
    visited = set()
    for i in range(len(patterns)):
        if i in visited: continue
        group = [i]
        for j in range(i + 1, len(patterns)):
            if sim_matrix[i, j] > threshold:
                group.append(j); visited.add(j)
        if len(group) > 1: groups.append(group)
    return groups

# --- 4. LOADERS ---
st.title("🕵️ PFR Candidate Checker")
col1, col2 = st.columns(2)
resp_file = col1.file_uploader("1. Upload Call List (Data)", type=["csv", "xlsx"])
screen_file = col2.file_uploader("2. Upload PFR Screener (Logic)", type=["xlsx"])

# --- 5. MAIN LOGIC ---
if resp_file and screen_file:
    if resp_file.name.endswith('.csv'):
        df = pd.read_csv(resp_file, encoding='utf-8-sig')
    else:
        df = pd.read_excel(resp_file)
    
    df.columns = [str(c).replace('\ufeff', '').replace('ï»¿', '').strip() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()].copy()
    headers = df.columns.tolist()
    norm_headers = [normalize(h) for h in headers]
    
    p_id_col = next((c for c in ['Participant ID', 'ID', 'Ref'] if c in headers), headers[0])
    phone_col = next((headers[i] for i, nh in enumerate(norm_headers) if any(p in nh for p in ['mob', 'tel', 'phone'])), None)

    # --- RESTORED STEP 1: SCREENER MAPPING ---
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
        q_id = re.search(r'q\d+', str(q_text).lower()).group(0) if re.search(r'q\d+', str(q_text).lower()) else normalize(q_text)[:15]
        def_idx = next((i for i, nh in enumerate(norm_headers) if q_id in nh), 0)
        
        with st.expander(f"❓ {str(q_text).strip()[:100]}", expanded=False):
            c1, c2 = st.columns([1, 2])
            mapping[q_text] = c1.selectbox(f"CSV Col:", headers, index=def_idx, key=f"m_{hash(q_text)}")
            auto_rej = [str(r).strip() for r in q_rows[q_rows[so_col].astype(str).str.contains("Disqualify", case=False, na=False)][a_col].tolist()] if so_col else []
            final_rules[q_text] = c2.multiselect("Reject if:", options, default=[r for r in auto_rej if r in options], key=f"r_{hash(q_text)}")

    # --- RESTORED STEP 2: COMPARISON ---
    st.header("⚖️ Step 2: Comparison")
    if 'consistency_pairs' not in st.session_state: st.session_state.consistency_pairs = 1
    consistency_rules = []
    for i in range(st.session_state.consistency_pairs):
        c1, c2 = st.columns(2)
        ca = c1.selectbox(f"Profile Col {i+1}", ["None"] + headers, key=f"pa_{i}")
        cb = c2.selectbox(f"Screener Col {i+1}", ["None"] + headers, key=f"pb_{i}")
        if ca != "None" and cb != "None": consistency_rules.append((ca, cb))
    if st.button("➕ Add Pair"):
        st.session_state.consistency_pairs += 1
        st.rerun()

    # --- STEP 3: UNIFIED AUDIT ---
    if st.button("🚀 Run Full Audit"):
        # Pre-clean generated cols to avoid duplicate error
        df = df.drop(columns=[c for c in ['Status', 'Reason', 'Carrier', 'Risk %', 'Pattern', 'ClusterFlag'] if c in df.columns])
        
        # 1. Clustering & Patterning
        df['Pattern'] = df[list(set(mapping.values()))].astype(str).agg('-'.join, axis=1)
        clusters = detect_clusters(df['Pattern'].tolist(), cluster_threshold)
        cluster_flags = set()
        for g in clusters: cluster_flags.update(g)
        df['ClusterFlag'] = df.index.isin(cluster_flags)

        # 2. Parallel API
        api_results = {}
        if phone_col and ipqs_key:
            api_results = parallel_ipqs(df[phone_col].dropna().unique(), ipqs_key, iso, dial)

        def audit_row(row):
            behav_score = 0
            # Cluster/Pattern Penalty
            if row['ClusterFlag']: behav_score += weight_pattern
            # Mismatch Penalty
            for ca, cb in consistency_rules:
                if normalize(row.get(ca)) != normalize(row.get(cb)): behav_score += weight_mismatch
            
            # Screener Logic (Hard Rejects)
            for q, bads in final_rules.items():
                if str(row.get(mapping[q])).strip() in bads:
                    return pd.Series(["Rejected", f"Screener: {q[:20]}", "N/A", behav_score])

            # API Data
            api_fraud, carrier = 0, "N/A"
            if phone_col in row and row[phone_col] in api_results:
                res = api_results[row[phone_col]]
                api_fraud = res['fraud_score']
                carrier = res['carrier']
                if reject_voip and res['voip']: return pd.Series(["Rejected", "VOIP Detected", carrier, 100])

            total_risk = min(100, behav_score + api_fraud)
            status = "Rejected" if total_risk > 70 else "Qualified"
            reason = "Pass" if status == "Qualified" else "High Risk Score"
            return pd.Series([status, reason, carrier, total_risk])

        with st.spinner("Processing Full Audit..."):
            df[['Status', 'Reason', 'Carrier', 'Risk %']] = df.apply(audit_row, axis=1)

       # --- RESULTS VIEW ---
        st.header("📊 Results & Quality Control")
        
        # Dashboard Metrics
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("✅ Qualified", (df['Status'] == "Qualified").sum())
        m2.metric("❌ Rejected", (df['Status'] == "Rejected").sum())
        m3.metric("🚩 Clustered Bots", df['ClusterFlag'].sum())
        m4.metric("🛡️ Avg Risk", f"{round(df['Risk %'].mean(), 1)}%")

        # Create Tabs for different workflows
        t_list, t_review, t_export = st.tabs(["📋 Full List", "🚩 Manual Review", "📥 Export"])

        with t_list:
            st.subheader("Candidate Audit Trail")
            view_choice = st.radio("Filter By:", ["All", "Qualified Only", "Rejected Only"], horizontal=True, key="view_filter")
            
            search = st.text_input("🔍 Search by ID, Name, or Email:", key="main_search")
            
            display_df = df.copy()
            if view_choice == "Qualified Only": display_df = display_df[display_df['Status'] == "Qualified"]
            if view_choice == "Rejected Only": display_df = display_df[display_df['Status'] == "Rejected"]
            
            if search:
                mask = display_df.astype(str).apply(lambda x: x.str.contains(search, case=False)).any(axis=1)
                display_df = display_df[mask]

            # Re-order columns so audit data is first
            audit_cols = ["Status", "Risk %", "Reason", "Carrier"]
            final_cols = audit_cols + [c for c in headers if c not in audit_cols]
            st.dataframe(display_df[final_cols], use_container_width=True)

        with t_review:
            st.subheader("Flagged Candidates for Review")
            # Only show those who didn't pass "Qualified"
            flagged_df = df[df['Status'] != "Qualified"].sort_values(by="Risk %", ascending=False)
            
            if flagged_df.empty:
                st.success("🎉 No flagged candidates found!")
            else:
                st.info(f"Showing {len(flagged_df)} candidates that require manual oversight.")
                
                for idx, row in flagged_df.iterrows():
                    # Create a clean "Review Card" for each person
                    with st.container(border=True):
                        c1, c2, c3 = st.columns([2, 2, 1])
                        
                        c1.write(f"**ID:** {row[p_id_col]}")
                        c1.write(f"**Reason:** {row['Reason']}")
                        
                        c2.write(f"**Risk Level:** {row['Risk %']}%")
                        c2.write(f"**Carrier:** {row['Carrier']}")
                        
                        # Manual "Force Check" button to see raw API data
                        if c3.button("Force API Check", key=f"force_{idx}"):
                            if phone_col in row:
                                with st.spinner("Fetching deep-scan data..."):
                                    raw_data = fetch_ipqs(row[phone_col], ipqs_key, iso, dial)
                                    if raw_data:
                                        st.json(raw_data)
                                    else:
                                        st.error("Could not fetch raw data.")

                        # Show their pattern in a small code block for quick comparison
                        st.caption(f"**Response Pattern:** {row['Pattern']}")

        with t_export:
            st.subheader("Prepare Deliverables")
            st.write("Download the final audit file. This includes all Status, Risk, and Carrier columns.")
            
            csv = df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 Download Final PFR Audit (CSV)",
                data=csv,
                file_name="pfr_audit_report.csv",
                mime="text/csv",
            )
