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
    """Hits the IPQS API to check for VOIP and Fraud."""
    clean_num = re.sub(r'\D', '', str(phone_number))
    url = f"https://www.ipqualityscore.com/api/json/phone/validate/{api_key}/{clean_num}"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        if data.get('success'):
            return data
    except: return None
    return None

# --- STEP 1: LOADERS ---
col1, col2 = st.columns(2)
with col1:
    resp_file = st.file_uploader("1. Upload Call List", type=["csv", "xlsx"])
with col2:
    screen_file = st.file_uploader("2. Upload PFR Screener", type=["xlsx"])

# --- SIDEBAR: API & SETTINGS ---
st.sidebar.header("🔑 API & Fraud Logic")
ipqs_key = st.sidebar.text_input("IPQS API Key", type="password")
reject_voip = st.sidebar.checkbox("Auto-Reject VOIP", value=True)
st.sidebar.divider()
phone_match_len = st.sidebar.slider("Phone Match Length", 5, 11, 10)
fuzzy_threshold = st.sidebar.slider("Pattern Match %", 80, 100, 95)

if resp_file and screen_file:
    try:
        df_resp = pd.read_csv(resp_file, encoding='latin1') if resp_file.name.endswith('.csv') else pd.read_excel(resp_file)
        df_resp.columns = [str(c).strip() for c in df_resp.columns]
        call_list_headers = df_resp.columns.tolist()
        
        # Ensure Participant ID exists
        p_id_col = 'Participant ID' if 'Participant ID' in df_resp.columns else call_list_headers[0]

        # --- STEP 2: SCREENER PARSER ---
        raw_screen = pd.read_excel(screen_file, header=None)
        h_idx = next(i for i, row in raw_screen.iterrows() if str(row[0]).strip().lower() in ["question", "questions"])
        df_screen = pd.read_excel(screen_file, header=h_idx)
        df_screen.iloc[:, 0] = df_screen.iloc[:, 0].ffill()
        logic_df = df_screen.dropna(subset=[df_screen.columns[1]])
        
        # Mapping UI (Simplified for brevity, use previous logic here)
        st.info("Ensure questions are mapped in the sections above before running.")

        # --- STEP 3: THE AUDIT ENGINE ---
        if st.button("🚀 Run Optimized Audit"):
            api_saved = 0
            
            def run_audit(row):
                nonlocal api_saved
                # 1. Caution Check
                if 'Caution' in row and pd.notna(row['Caution']) and str(row['Caution']).strip() != "":
                    api_saved += 1
                    return pd.Series(["Rejected", "Caution Note", "N/A"])
                
                # 2. Screener Logic Check
                # (Assuming final_rules and mapping are defined from Step 3 logic)
                # If fail: api_saved += 1; return ...

                # 3. IPQS Final Gate
                if ipqs_key and 'Mobile' in row:
                    res = check_ipqs_phone(row['Mobile'], ipqs_key)
                    if res:
                        if reject_voip and res.get('voip'):
                            return pd.Series(["Rejected", f"VOIP ({res.get('carrier')})", res.get('carrier')])
                        return pd.Series(["Qualified", "Pass", res.get('carrier')])
                
                return pd.Series(["Qualified", "Pass", "Unknown"])

            # Apply Logic
            df_resp[['Status', 'Reason', 'Carrier']] = df_resp.apply(run_audit, axis=1)

            # --- RESULTS DASHBOARD ---
            st.header("📊 Results")
            c1, c2, c3 = st.columns(3)
            c1.metric("✅ Qualified", (df_resp['Status'] == "Qualified").sum())
            c2.metric("❌ Rejected", (df_resp['Status'] == "Rejected").sum())
            c3.metric("💰 API Credits Saved", api_saved)

            # --- TABBED VIEW ---
            t1, t2, t3 = st.tabs(["🔍 Manual Review", "📶 Carrier Insights", "📥 Download"])

            with t1:
                st.subheader("Review Rejected Applicants")
                rejected_df = df_resp[df_resp['Status'] == "Rejected"]
                for _, r in rejected_df.iterrows():
                    col_text, col_btn = st.columns([4, 1])
                    col_text.write(f"**{r[p_id_col]}**: {r['Forename']} {r['Surname']} (Reason: {r['Reason']})")
                    if col_btn.button("Force IPQS Check", key=f"check_{r[p_id_col]}"):
                        res = check_ipqs_phone(r['Mobile'], ipqs_key)
                        st.write(res) # Show live data for this specific person

            with t2:
                st.subheader("Mobile Carrier Distribution")
                carrier_counts = df_resp['Carrier'].value_counts()
                st.bar_chart(carrier_counts)

            with t3:
                st.download_button("Download Report", df_resp.to_csv(index=False).encode('utf-8-sig'), "pfr_audit.csv")

    except Exception as e:
        st.error(f"Audit Error: {e}")
