import streamlit as st
import pandas as pd
import io
import re
from thefuzz import fuzz

# --- INITIAL SETUP ---
st.set_page_config(page_title="PFR Fraud Detective", layout="wide")
st.title("🕵️ PFR Full Fraud Detective")
st.markdown("### Advanced Grading + Fuzzy Pattern Analysis")

# --- HELPER FUNCTIONS ---
def normalize(text):
    """Standardizes text for comparison."""
    return re.sub(r'[^a-z0-9]', '', str(text).lower()).strip()

def canonical_email(email):
    """Detects Gmail aliases (dots and plus signs)."""
    try:
        email_str = str(email).lower().strip()
        if '@' not in email_str: return email_str
        name, domain = email_str.split('@')
        if 'gmail' in domain or 'googlemail' in domain:
            name = name.split('+')[0].replace('.', '')
        return f"{name}@{domain}"
    except:
        return str(email).lower()

# --- STEP 1: FILE UPLOADERS ---
col1, col2 = st.columns(2)
with col1:
    resp_file = st.file_uploader("1. Upload Call List (CSV or XLSX)", type=["csv", "xlsx"])
with col2:
    screen_file = st.file_uploader("2. Upload PFR Screener", type=["xlsx"])

# --- SIDEBAR: FRAUD SENSITIVITY ---
st.sidebar.header("🛡️ Fraud Sensitivity Settings")
phone_match_len = st.sidebar.slider("Phone Match Length", 5, 11, 10)
fuzzy_threshold = st.sidebar.slider("Fuzzy Answer Match (%)", 80, 100, 95)
speed_threshold = st.sidebar.number_input("Fast Response Threshold (Secs)", value=45)

if resp_file and screen_file:
    try:
        # Load Data
        df_resp = pd.read_csv(resp_file, encoding='latin1') if resp_file.name.endswith('.csv') else pd.read_excel(resp_file)
        df_resp.columns = [str(c).strip() for c in df_resp.columns]
        call_list_headers = df_resp.columns.tolist()

        # --- STEP 2: SCREENER PARSER ---
        raw_screen = pd.read_excel(screen_file, header=None)
        h_idx = next(i for i, row in raw_screen.iterrows() if str(row[0]).strip().lower() in ["question", "questions"])
        df_screen = pd.read_excel(screen_file, header=h_idx)
        df_screen.iloc[:, 0] = df_screen.iloc[:, 0].ffill()
        q_col, a_col = df_screen.columns[0], df_screen.columns[1]
        so_col = next((c for c in df_screen.columns if any(k in c.lower() for k in ["screen-out", "disqualify"])), None)
        logic_df = df_screen.dropna(subset=[a_col])

        # --- STEP 3: REJECTION MAPPING & AUTO-DETECT ---
        st.header("⚙️ Step 1: Verify Rejection Logic")
        final_rules, mapping = {}, {}

        for q_text in logic_df[q_col].unique():
            q_rows = logic_df[logic_df[q_col] == q_text]
            options = [str(o).strip() for o in q_rows[a_col].unique().tolist() if pd.notna(o)]
            
            auto_rejected = []
            if so_col:
                raw_auto = q_rows[q_rows[so_col].astype(str).str.contains("Disqualify", case=False, na=False)][a_col].tolist()
                auto_rejected = [str(r).strip() for r in raw_auto if str(r).strip() in options]

            with st.expander(f"📂 {str(q_text).strip()[:100]}", expanded=False):
                cl, cr = st.columns([1, 2])
                with cl:
                    q_num_match = re.search(r'q\d+', str(q_text).lower())
                    q_id = q_num_match.group(0) if q_num_match else normalize(q_text)[:5]
                    def_idx = next((i for i, h in enumerate(call_list_headers) if q_id in normalize(h)), 0)
                    mapping[q_text] = st.selectbox(f"Link CSV Col:", call_list_headers, index=def_idx, key=f"m_{hash(q_text)}")
                with cr:
                    rejections = st.multiselect("Reject if Answer is:", options=options, default=auto_rejected, key=f"r_{hash(q_text)}")
                    final_rules[q_text] = [str(r).lower().strip() for r in rejections]

        # --- STEP 4: DYNAMIC CONSISTENCY ---
        st.header("⚖️ Step 2: Consistency Checks")
        if 'consistency_pairs' not in st.session_state: st.session_state.consistency_pairs = 1
        consistency_rules = []
        for i in range(st.session_state.consistency_pairs):
            c1, c2 = st.columns(2)
            with c1: col_a = st.selectbox(f"Pair {i+1}: Profile", ["None"] + call_list_headers, key=f"pa_{i}")
            with c2: col_b = st.selectbox(f"Pair {i+1}: Screener", ["None"] + call_list_headers, key=f"pb_{i}")
            if col_a != "None" and col_b != "None": consistency_rules.append((col_a, col_b))
        
        if st.button("➕ Add Another Match Pair"):
            st.session_state.consistency_pairs += 1
            st.rerun()

        st.divider()

        # --- STEP 5: RUN AUDIT ---
        if st.button("🚀 Run Comprehensive Audit"):
            # A. GRADING
            def grade_row(row):
                for q, bad_vals in final_rules.items():
                    if not bad_vals: continue
                    col = mapping.get(q)
                    ans = str(row.get(col)).strip().lower() if pd.notna(row.get(col)) else ""
                    if ans in bad_vals: return pd.Series(["Rejected", f"Failed: {q}"])
                return pd.Series(["Qualified", "Pass"])
            
            df_resp[['Status', 'Reason']] = df_resp.apply(grade_row, axis=1)
            df_resp['Suspicion_Score'] = 0
            df_resp['Suspicion_Reasons'] = ""

            # B. EMAIL & PHONE
            if 'Email' in df_resp.columns:
                df_resp['Email_Canonical'] = df_resp['Email'].apply(canonical_email)
                email_dupes = df_resp[df_resp.duplicated('Email_Canonical', keep=False)]
                for idx in email_dupes.index:
                    df_resp.at[idx, 'Suspicion_Score'] += 5
                    df_resp.at[idx, 'Suspicion_Reasons'] += "Email Alias/Duplicate; "

            if 'Mobile' in df_resp.columns:
                df_resp['P_Clean'] = df_resp['Mobile'].astype(str).str.replace(r'\D', '', regex=True).str[:phone_match_len]
                phone_dupes = df_resp[df_resp.duplicated('P_Clean', keep=False) & (df_resp['P_Clean'] != "")]
                for idx in phone_dupes.index:
                    df_resp.at[idx, 'Suspicion_Score'] += 2
                    df_resp.at[idx, 'Suspicion_Reasons'] += f"Phone Cluster; "

            # C. FUZZY PATTERN MATCHING
            q_cols = list(set(mapping.values()))
            df_resp['Pattern_String'] = df_resp[q_cols].astype(str).agg(' '.join, axis=1)
            
            already_flagged = set()
            for i in range(len(df_resp)):
                if i in already_flagged: continue
                p_i = df_resp.iloc[i]['Pattern_String']
                cluster = [i]
                for j in range(i+1, len(df_resp)):
                    if fuzz.ratio(p_i, df_resp.iloc[j]['Pattern_String']) >= fuzzy_threshold:
                        cluster.append(j)
                        already_flagged.add(j)
                
                if len(cluster) > 1:
                    for idx in cluster:
                        df_resp.at[df_resp.index[idx], 'Suspicion_Score'] += 6
                        df_resp.at[df_resp.index[idx], 'Suspicion_Reasons'] += f"{fuzzy_threshold}% Near-Identical Pattern; "

            # D. CONSISTENCY & SPEED
            for ca, cb in consistency_rules:
                mismatch = df_resp[df_resp[ca].apply(normalize) != df_resp[cb].apply(normalize)]
                for idx in mismatch.index:
                    df_resp.at[idx, 'Suspicion_Score'] += 4
                    df_resp.at[idx, 'Suspicion_Reasons'] += f"Mismatch: {ca} vs {cb}; "

            if 'Response Time' in df_resp.columns:
                df_resp['Seconds'] = pd.to_numeric(df_resp['Response Time'], errors='coerce')
                for idx in df_resp[df_resp['Seconds'] < speed_threshold].index:
                    df_resp.at[idx, 'Suspicion_Score'] += 4
                    df_resp.at[idx, 'Suspicion_Reasons'] += "Superhuman Speed; "

            # --- OUTPUT ---
            st.header("📊 Audit Dashboard")
            m1, m2, m3 = st.columns(3)
            m1.metric("✅ Qualified", (df_resp['Status'] == "Qualified").sum())
            m2.metric("❌ Rejected", (df_resp['Status'] == "Rejected").sum())
            m3.metric("🚩 Flagged", (df_resp['Suspicion_Score'] > 0).sum())

            # Clusters Display
            if 'Mobile' in df_resp.columns and not phone_dupes.empty:
                st.subheader("📱 Linked Phone Clusters")
                for prefix, group in phone_dupes.groupby('P_Clean'):
                    with st.expander(f"🚩 Cluster: {prefix}XXXX ({len(group)})"):
                        st.table(group[['Forename', 'Surname', 'Email', 'Mobile', 'Status']])

            st.subheader("🚩 Suspicious Participants")
            st.dataframe(df_resp[df_resp['Suspicion_Score'] > 0].sort_values(by='Suspicion_Score', ascending=False))
            
            st.subheader("Full Data View")
            st.dataframe(df_resp)
            st.download_button("📥 Download Final Audit", df_resp.to_csv(index=False).encode('utf-8-sig'), "pfr_audit.csv")

    except Exception as e:
        st.error(f"Audit Error: {e}")
