import streamlit as st
import pandas as pd
import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    st.error("Missing dependency: Please run 'pip install scikit-learn'")
    st.stop()

# --- CONFIG ---
st.set_page_config(page_title="PFR Checker PRO", layout="wide")

# --- CONSTANTS ---
STATUS_REJECTED = "Rejected"
STATUS_REVIEW = "Review"
STATUS_QUALIFIED = "Qualified"

# --- HELPERS ---
def normalize(x):
    return re.sub(r'[^a-z0-9]', '', str(x).lower())

def compute_status(score):
    if score > 70:
        return STATUS_REJECTED
    elif score > 40:
        return STATUS_REVIEW
    return STATUS_QUALIFIED

# --- API (PARALLEL) ---
def fetch_ipqs(phone, api_key, iso, dial):
    try:
        clean = re.sub(r'[^0-9+]', '', str(phone))
        if not clean.startswith('+'):
            if clean.startswith('0'):
                clean = clean[1:]
            clean = dial + clean
        else:
            clean = clean.replace('+', '')

        url = f"https://www.ipqualityscore.com/api/json/phone/{api_key}/{clean}"
        res = requests.get(url, params={'country': iso}, timeout=5)
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

def parallel_ipqs(df, phone_col, api_key, iso, dial):
    phones = df[phone_col].dropna().unique()
    results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_ipqs, p, api_key, iso, dial): p for p in phones}
        for f in as_completed(futures):
            r = f.result()
            if r: results[r["phone"]] = r
    return results

# --- CLUSTERING ---
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

# --- UI ---
st.title("🕵️ PFR Candidate Checker PRO")

col1, col2 = st.columns(2)
resp_file = col1.file_uploader("Upload Call List", type=["csv", "xlsx"])
screen_file = col2.file_uploader("Upload Screener", type=["xlsx"])

# --- SIDEBAR ---
st.sidebar.header("Settings")
# Using hardcoded key since we removed secrets requirement for now
ipqs_key = "H67E8mmH292LeSaTgbrufW5qzj68VEnG" 

country_map = {"UK": ("GB", "44"), "US": ("US", "1"), "AU": ("AU", "61")}
c = st.sidebar.selectbox("Country", list(country_map.keys()))
iso, dial = country_map[c]

cluster_threshold = st.sidebar.slider("Cluster Sensitivity", 0.7, 0.99, 0.9)

# --- MAIN ---
if resp_file and screen_file:
    # Load Data with BOM fix
    if resp_file.name.endswith('.csv'):
        df = pd.read_csv(resp_file, encoding='utf-8-sig')
    else:
        df = pd.read_excel(resp_file)
    
    df.columns = [str(c).replace('\ufeff', '').replace('ï»¿', '').strip() for c in df.columns]
    headers = df.columns.tolist()

    # PHONE DETECTION
    phone_col = next((c for c in headers if any(p in c.lower() for p in ["phone", "mobile", "tel"])), None)

    # SIMPLE PATTERN BUILD (Excluding ID column if possible)
    df["Pattern"] = df.astype(str).agg("-".join, axis=1)

    # --- CLUSTER DETECTION ---
    st.subheader("🔍 Detecting Behavioural Clusters...")
    clusters = detect_clusters(df["Pattern"].tolist(), cluster_threshold)
    cluster_flags = set()
    for g in clusters: cluster_flags.update(g)
    df["ClusterFlag"] = df.index.isin(cluster_flags)

    # --- API ENRICHMENT ---
    if phone_col and ipqs_key:
        st.subheader("📡 Running Phone Intelligence...")
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

    # --- SUMMARY & DISPLAY ---
    st.subheader("📊 Summary")
    m1, m2, m3 = st.columns(3)
    m1.metric("Rejected", (df["Status"] == STATUS_REJECTED).sum())
    m2.metric("Review", (df["Status"] == STATUS_REVIEW).sum())
    m3.metric("Qualified", (df["Status"] == STATUS_QUALIFIED).sum())

    st.dataframe(df[[phone_col, "Status", "Score", "Reason", "Carrier"] + headers])

    # --- EXPORT ---
    @st.cache_data
    def to_excel(data):
        from io import BytesIO
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            data.to_excel(writer, index=False)
        return output.getvalue()

    st.download_button("📥 Download Excel Report", to_excel(df), "pfr_pro_report.xlsx")
    
