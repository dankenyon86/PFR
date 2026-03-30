import streamlit as st
import pandas as pd
import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from thefuzz import fuzz

# --- 1. CONFIG & LOGO ---
st.set_page_config(page_title="PFR Candidate Checker", layout="wide")
try:
    st.sidebar.image("PFRLogo.png", use_container_width=True)
except:
    pass

# --- 2. SIDEBAR SETTINGS ---
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
        return data if data.get("success") else None
    except: return None

# --- 4. LOADERS ---
st.title("🕵️ PFR Candidate Checker")
col1, col2 = st.columns(2)
resp_file = col1.file_uploader("1. Upload Call List (Data)", type=["csv", "xlsx"])
screen_file = col2.file_uploader("2. Upload PFR Screener (Logic)", type=["xlsx"])

if resp_file and screen_file:
    # --- DATA LOADING & DEDUPLICATION ---
    df = pd.read_csv(resp_file, encoding='utf-8-sig') if resp_file.name.endswith('.csv') else pd.read_excel(resp_file)
    df.columns = [str(c).replace('\ufeff', '').replace('ï»¿', '').strip() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()].copy()
    headers = df.columns.tolist()
    norm_headers = [normalize(h) for h in headers]
    
    p_id_col = next((c for c in ['Participant ID', 'ID', 'Ref'] if c in headers), headers[0])
    phone_col = next((headers[i] for i, nh in enumerate(norm_headers) if any(p in nh for p in ['mob', 'tel', 'phone'])), None)

    # --- STEP 1: RESTORED SCREENER MAPPING ---
    raw_screen = pd.read_excel(screen_file, header=None)
    h_idx = next(i for i, row in raw_screen.iterrows() if "question" in str(row[0]).lower())
    df_screen = pd.read_excel(screen_file, header=h_idx)
    df_screen.iloc[:, 0] = df_screen.iloc[:, 0].ffill() # Restored Question Fill
    q_col, a_col = df_screen.columns[0], df_screen.columns[1]
    so_col = next((c for c in df_screen.columns if any(k in str(c).lower() for k in ["screen-out", "disqualify"])), None)
    
    st.header("⚙️ Step 1: Mapping")
    final_rules, mapping = {}, {}
    for q_text in df_screen[q_col].unique():
        if pd.isna(q_text): continue
        q_rows = df_screen[df_screen[q_col] == q_text]
        options = [str(o).strip() for o in q_rows[a_col].unique().tolist() if pd.notna(o)]
        
        # Auto-match logic
        q_id = re.search(r'q\d+', str(q_text).lower()).group(0) if re.search(r'q\d+', str(q_text).lower()) else normalize(q_text)[:15]
        def_idx = next((i for i, nh in enumerate(norm_headers) if q_id in nh), 0)

        with st.expander(f"❓ {str(q_text).strip()[:100]}", expanded=False):
            c1, c2 = st.columns([1, 2])
            mapping[q_text] = c1.selectbox(f"CSV Col:", headers, index=def_idx, key=f"m_{hash(q_text)}")
            auto_rej = []
            if so_col:
                auto_rej = [str(r).strip() for r in q_rows[q_rows[so_col].astype(str).str.contains("Disqualify", case=False, na=False)][a_col].tolist()]
            final_rules[q_text] = c2.multiselect("Reject if:", options, default=[r for r in auto_rej if r in options], key=f"r_{hash(q_text)}")

    # --- STEP 2: RESTORED COMPARISON PAIRS ---
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

    # --- STEP 3: THE AUDIT ENGINE ---
    if st.button("🚀 Run Full Audit"):
        # Cleanup
        df = df.drop(columns=[c for c in ['Status', 'Reason', 'Carrier', 'Risk %', 'Pattern', 'ClusterFlag'] if c in df.columns])
        prog = st.progress(0)
        
        # 1. Pattern Clustering
        df['Pattern'] = df[list(set(mapping.values()))].astype(str).agg('-'.join, axis=1)
        vectorizer = TfidfVectorizer()
        X = vectorizer.fit_transform(df['Pattern'].tolist())
        sim_matrix = cosine_similarity(X)
        groups, visited = [], set()
        for i in range(len(df)):
            if i in visited: continue
            group = [i]
            for j in range(i+1, len(df)):
                if sim_matrix[i, j] > cluster_threshold: group.append(j); visited.add(j)
            if len(group) > 1: groups.append(group)
        df['ClusterFlag'] = df.index.isin([idx for g in groups for idx in g])
        prog.progress(30)

        # 2. Parallel API
        api_results = {}
        if phone_col:
            unique_phones = df[phone_col].dropna().unique()
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(fetch_ipqs, p, ipqs_key, iso, dial): p for p in unique_phones}
                for i, f in enumerate(as_completed(futures)):
                    r = f.result()
                    if r: api_results[r["phone"]] = r
                    prog.progress(30 + int((i/len(unique_phones))*60))

        # 3. Final Scoring
        def audit_row(row):
            behav_score = weight_pattern if row['ClusterFlag'] else 0
            for ca, cb in consistency_rules:
                if normalize(row.get(ca)) != normalize(row.get(cb)): behav_score += weight_mismatch
            
            for q, bads in final_rules.items():
                if str(row.get(mapping[q])).strip() in bads:
                    return pd.Series(["Rejected", "Screener Fail", "N/A", behav_score])
            
            api_data = api_results.get(row.get(phone_col), {})
            fraud_score = api_data.get('fraud_score', 0)
            carrier = api_data.get('carrier', 'Valid')
            if reject_voip and api_data.get('voip'): return pd.Series(["Rejected", "VOIP Number", carrier, 100])
            
            risk = min(100, behav_score + fraud_score)
            status = "Rejected" if risk > 70 else "Qualified"
            return pd.Series([status, "Pass" if status=="Qualified" else "Risk Flag", carrier, risk])

        df[['Status', 'Reason', 'Carrier', 'Risk %']] = df.apply(audit_row, axis=1)
        prog.progress(100)
        
        # --- 7. RESULTS UI ---
        st.header("📊 Audit Results")
        t_data, t_cluster, t_export = st.tabs(["📋 Full Dataset", "🚩 Cluster Review", "📥 Export"])
        
        with t_data:
            view = st.radio("Show:", ["All", "Qualified", "Rejected"], horizontal=True)
            search = st.text_input("Search ID/Name:")
            d_df = df.copy()
            if view == "Qualified": d_df = d_df[d_df['Status'] == "Qualified"]
            if view == "Rejected": d_df = d_df[d_df['Status'] == "Rejected"]
            if search: d_df = d_df[d_df.astype(str).apply(lambda x: x.str.contains(search, case=False)).any(axis=1)]
            
            audit_cols = ["Status", "Risk %", "Reason", "Carrier"]
            st.dataframe(d_df[audit_cols + [c for c in headers if c not in audit_cols]])

        with t_cluster:
            st.subheader("Answer Pattern Clusters")
            for i, group in enumerate(groups):
                c_df = df.iloc[group]
                with st.expander(f"🚩 Cluster {i+1} ({len(group)} members)"):
                    st.info(f"Pattern: {c_df['Pattern'].iloc[0]}")
                    st.table(c_df[[p_id_col, "Status", "Risk %"]])

        with t_export:
            st.download_button("📥 Download Audit Report", df.to_csv(index=False).encode('utf-8-sig'), "pfr_audit.csv")
