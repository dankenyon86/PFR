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
    st.sidebar.info("💡 Note: Place 'PFRLogo.png' in the root folder.")

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

# --- 4. LOADERS ---
st.title("🕵️ PFR Candidate Checker")
col1, col2 = st.columns(2)
resp_file = col1.file_uploader("1. Upload Call List (Data)", type=["csv", "xlsx"])
screen_file = col2.file_uploader("2. Upload PFR Screener (Logic)", type=["xlsx"])

# --- 5. MAIN LOGIC ---
if resp_file and screen_file:
    # Load Data
    df = pd.read_csv(resp_file, encoding='utf-8-sig') if resp_file.name.endswith('.csv') else pd.read_excel(resp_file)
    df.columns = [str(c).replace('\ufeff', '').replace('ï»¿', '').strip() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()].copy()
    headers = df.columns.tolist()
    norm_headers = [normalize(h) for h in headers]
    
    p_id_col = next((c for c in ['Participant ID', 'ID', 'Ref'] if c in headers), headers[0])
    phone_col = next((headers[i] for i, nh in enumerate(norm_headers) if any(p in nh for p in ['mob', 'tel', 'phone'])), None)

    # Screener Logic
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

    # --- 6. AUDIT ---
    if st.button("🚀 Run Full Audit"):
        df = df.drop(columns=[c for c in ['Status', 'Reason', 'Carrier', 'Risk %', 'Pattern', 'ClusterFlag'] if c in df.columns])
        prog_bar = st.progress(0)

        # 1. Clustering
        df['Pattern'] = df[list(set(mapping.values()))].astype(str).agg('-'.join, axis=1)
        vectorizer = TfidfVectorizer()
        X = vectorizer.fit_transform(df['Pattern'].tolist())
        sim_matrix = cosine_similarity(X)
        groups, visited = [], set()
        for i in range(len(df)):
            if i in visited: continue
            group = [i]
            for j in range(i + 1, len(df)):
                if sim_matrix[i, j] > cluster_threshold: group.append(j); visited.add(j)
            if len(group) > 1: groups.append(group)
        cluster_flags = set([idx for g in groups for idx in g])
        df['ClusterFlag'] = df.index.isin(cluster_flags)
        prog_bar.progress(30)

        # 2. Parallel API
        unique_phones = df[phone_col].dropna().unique()
        api_results = {}
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(fetch_ipqs, p, ipqs_key, iso, dial): p for p in unique_phones}
            for i, f in enumerate(as_completed(futures)):
                r = f.result()
                if r: api_results[r["phone"]] = r
                prog_bar.progress(30 + int((i / len(unique_phones)) * 60))

        # 3. Final Scoring
        def audit_row(row):
            behav_score = 0
            if row['ClusterFlag']: behav_score += weight_pattern
            for ca, cb in consistency_rules:
                if normalize(row.get(ca)) != normalize(row.get(cb)): behav_score += weight_mismatch
            
            # Hard Rejects (Screener)
            for q, bads in final_rules.items():
                if str(row.get(mapping[q])).strip() in bads:
                    return pd.Series(["Rejected", f"Screener Fail", "N/A", behav_score])
            
            api_fraud, carrier = 0, "N/A"
            if row.get(phone_col) in api_results:
                res = api_results[row[phone_col]]
                api_fraud, carrier = res['fraud_score'], res['carrier']
                if reject_voip and res['voip']: return pd.Series(["Rejected", "VOIP Number", carrier, 100])
            
            total_risk = min(100, behav_score + api_fraud)
            status = "Rejected" if total_risk > 70 else "Qualified"
            reason = "Pass" if status == "Qualified" else "High Behavioral/Phone Risk"
            return pd.Series([status, reason, carrier, total_risk])

        df[['Status', 'Reason', 'Carrier', 'Risk %']] = df.apply(audit_row, axis=1)
        prog_bar.progress(100)
        st.success("✅ Audit Complete!")

        # --- UPDATED RESULTS VIEW ---
        t_data, t_qc, t_export = st.tabs(["📋 Full Dataset", "🚩 High Risk QC", "📥 Export"])
        
        with t_data:
            view_choice = st.radio("View:", ["All", "Qualified", "Rejected"], horizontal=True)
            d_df = df.copy()
            if view_choice == "Qualified": d_df = d_df[d_df['Status'] == "Qualified"]
            if view_choice == "Rejected": d_df = d_df[d_df['Status'] == "Rejected"]
            st.dataframe(d_df[["Status", "Risk %", "Reason", "Carrier"] + headers])

        with t_qc:
            st.subheader("Potential Bots / Fraud (Passed Screener)")
            # ONLY SHOW PEOPLE WHO PASSED THE SCREENER BUT HAVE RISK > 0
            qc_df = df[(df['Status'] == "Qualified") & (df['Risk %'] > 0)].sort_values("Risk %", ascending=False)
            
            if qc_df.empty:
                st.success("No suspicious 'Qualified' candidates detected.")
            else:
                for idx, r in qc_df.iterrows():
                    with st.container(border=True):
                        c1, c2, c3 = st.columns([3, 1, 1])
                        c1.write(f"**ID:** {r[p_id_col]} | **Pattern:** {r['Pattern'][:50]}...")
                        c2.write(f"**Risk:** {r['Risk %']}%")
                        if c3.button("Raw Check", key=f"qc_{idx}"):
                            st.json(fetch_ipqs(r[phone_col], ipqs_key, iso, dial))

        with t_export:
            st.download_button("Download Audit CSV", df.to_csv(index=False).encode('utf-8-sig'), "pfr_audit.csv")
