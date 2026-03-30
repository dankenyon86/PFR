import streamlit as st
import pandas as pd
import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- DEPENDENCY CHECK ---
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    st.error("Missing dependency: Please run 'pip install scikit-learn'")
    st.stop()

# --- 1. CONFIG ---
st.set_page_config(page_title="PFR Checker PRO", layout="wide")

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

cluster_threshold = st.sidebar.slider("Cluster Sensitivity (Bot Detection)", 0.7, 0.99, 0.9)

# --- 3. CONSTANTS ---
STATUS_REJECTED = "Rejected"
STATUS_REVIEW = "Review"
STATUS_QUALIFIED = "Qualified"

# --- 4. HELPER FUNCTIONS ---
def normalize(x):
    return re.sub(r'[^a-z0-9]', '', str(x).lower()).strip()

def compute_status(score):
    if score > 70: return STATUS_REJECTED
    elif score > 40: return STATUS_REVIEW
    return STATUS_QUALIFIED

# --- 5. PARALLEL API ENGINE ---
def fetch_ipqs(phone, api_key, iso_code, dial_code):
    try:
        clean = re.sub(r'[^0-9+]', '', str(phone))
        if not clean.startswith('+'):
            if clean.startswith('0'): clean = clean[1:]
            clean = dial_code + clean
        else:
            clean = clean.replace('+', '')

        url = f"https://www.ipqualityscore.com/api/json/phone/{api_key}/{clean}"
        res = requests.get(url, params={'country': iso_code, 'strictness': 1}, timeout=5)
        data = res.json()
        if data.get("success"):
            return {
                "phone": phone,
                "fraud_score": data.get("fraud_score", 0),
                "carrier": data.get("carrier", "Unknown"),
                "voip": data.get("voip", False)
            }
    except:
        return {"phone": phone, "fraud_score": 0, "carrier": "Error", "voip": False}

def parallel_ipqs(df, phone_col, api_key, iso_code, dial_code):
    phones = df[phone_col].dropna().unique()
    results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_ipqs, p, api_key, iso_code, dial_code): p for p in phones}
        for f in as_completed(futures):
            r = f.result()
            if r: results[r["phone"]] = r
    return results

# --- 6. CLUSTERING (BOT DETECTION) ---
def detect_clusters(patterns, threshold=0.9):
    if not patterns: return []
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
                group.append(j)
                visited.add(j)
        if len(group) > 1: groups.append(group)
    return groups

# --- 7. UI LOADERS ---
st.title("🕵️ PFR Candidate Checker PRO")
col1, col2 = st.columns(2)
resp_file = col1.file_uploader("1. Upload Call List (Data)", type=["csv", "xlsx"])
screen_file = col2.file_uploader("2. Upload Screener (Logic)", type=["xlsx"])

# --- 8. MAIN AUDIT LOGIC ---
if resp_file and screen_file:
    # Load Data + BOM Fix
    if resp_file.name.endswith('.csv'):
        df = pd.read_csv(resp_file, encoding='utf-8-sig')
    else:
        df = pd.read_excel(resp_file)
    
    # CRITICAL: Strip hidden characters and drop duplicate columns from the raw file immediately
    df.columns = [str(c).replace('\ufeff', '').replace('ï»¿', '').strip() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()].copy()
    
    headers = df.columns.tolist()
    p_id_col = next((c for c in ['Participant ID', 'ID', 'Ref'] if c in headers), headers[0])
    phone_col = next((c for c in headers if any(p in c.lower() for p in ["phone", "mobile", "tel"])), None)

    # Simple Pattern Build for Clustering
    df["Pattern"] = df.astype(str).agg("-".join, axis=1)

    # --- EXECUTION ---
    with st.spinner("🔍 Detecting Behavioural Clusters..."):
        clusters = detect_clusters(df["Pattern"].tolist(), cluster_threshold)
        cluster_flags = set()
        for g in clusters: cluster_flags.update(g)
        df["ClusterFlag"] = df.index.isin(cluster_flags)

    if phone_col and ipqs_key:
        with st.spinner("📡 Running Phone Intelligence (Parallel)..."):
            api_results = parallel_ipqs(df, phone_col, ipqs_key, iso, dial)
            df["FraudScore"] = df[phone_col].map(lambda x: api_results.get(x, {}).get("fraud_score", 0))
            df["VOIP"] = df[phone_col].map(lambda x: api_results.get(x, {}).get("voip", False))
            df["Carrier"] = df[phone_col].map(lambda x: api_results.get(x, {}).get("carrier", "Unknown"))
    else:
        df["FraudScore"], df["VOIP"], df["Carrier"] = 0, False, "N/A"

    # --- SCORING ---
    df["Score"] = df["FraudScore"] + (df["ClusterFlag"].astype(int) * 35)
    df["Status"] = df["Score"].apply(compute_status)

    def explain(row):
        reasons = []
        if row["ClusterFlag"]: reasons.append("Identical Answer Pattern")
        if row["FraudScore"] > 50: reasons.append("High API Fraud Score")
        if row["VOIP"]: reasons.append("VOIP Number Detected")
        return " | ".join(reasons) or "Clear"
    df["Reason"] = df.apply(explain, axis=1)

    # --- RESULTS DASHBOARD ---
    st.divider()
    st.subheader("📊 Summary Statistics")
    m1, m2, m3 = st.columns(3)
    m1.metric("Rejected", (df["Status"] == STATUS_REJECTED).sum())
    m2.metric("Review", (df["Status"] == STATUS_REVIEW).sum())
    m3.metric("Qualified", (df["Status"] == STATUS_QUALIFIED).sum())

    # --- VIEW CONTROLS ---
    st.subheader("🔍 Detailed Audit View")
    c1, c2 = st.columns([1, 2])
    view_choice = c1.radio("Filter List:", ["All", "Qualified Only", "Rejected Only"], horizontal=True)
    search_term = c2.text_input("Search by ID or Name:")

    # Filter Logic
    display_df = df.copy()
    if view_choice == "Qualified Only":
        display_df = display_df[display_df['Status'] == STATUS_QUALIFIED]
    elif view_choice == "Rejected Only":
        display_df = display_df[display_df['Status'] == STATUS_REJECTED]
    
    if search_term:
        mask = display_df.astype(str).apply(lambda x: x.str.contains(search_term, case=False)).any(axis=1)
        display_df = display_df[mask]

    # --- PREVENT DUPLICATE COLUMNS IN DISPLAY ---
    # We define our Audit result columns
    audit_cols = ["Status", "Score", "Reason", "Carrier"]
    if phone_col:
        audit_cols = [phone_col] + audit_cols
    
    # We build a final list that ensures the audit columns are first and original headers are second, without repeats
    final_display_cols = audit_cols + [c for c in headers if c not in audit_cols]
    
    st.dataframe(display_df[final_display_cols])

    # --- EXPORT ---
    @st.cache_data
    def to_excel(data):
        from io import BytesIO
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            data.to_excel(writer, index=False)
        return output.getvalue()

    st.download_button("📥 Download Full Excel Report", to_excel(df), "pfr_pro_audit_report.xlsx")
