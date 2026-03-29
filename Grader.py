import streamlit as st
import pandas as pd
import io
import re
from thefuzz import fuzz

# --- INITIAL SETUP ---
st.set_page_config(page_title="PFR Fraud Detective", layout="wide")
st.title("🕵️ PFR Full Fraud Detective")

# --- HELPER FUNCTIONS ---
def normalize(text):
    return re.sub(r'[^a-z0-9]', '', str(text).lower()).strip()

def canonical_email(email):
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
fuzzy_threshold = st.sidebar.slider("Pattern Match %", 80, 100, 95)
speed_threshold = st.sidebar.number_input("Fast Response Threshold (Secs)", value=45)

if resp_file and screen_file:
    try:
        df_resp = pd.read_csv(resp_file, encoding='latin1') if resp_file.name.endswith('.csv') else pd.read_excel(resp_file)
        df_resp.columns = [str(c).strip() for c in df_resp.columns]
        call_list_headers = df_resp.columns.tolist()

        # Define columns we always want to see in fraud tables
        info_cols = ['Participant ID', 'Forename', 'Surname', 'Email', 'Status']
        # Check if they exist, if not, use whatever is available
        display_cols = [c for c in info_cols if c in df_resp.columns]

        # --- STEP 2: SCREENER PARSER ---
        raw_screen = pd.read_excel(screen_file, header=None)
        h_idx = next(i for i, row in raw_screen.iterrows() if str(row[0]).strip().lower() in ["question", "questions"])
        df_screen = pd.read_excel(screen_file, header=h_idx)
        df_screen.iloc[:, 0] = df_screen.iloc[:, 0].ffill()
        q_col, a_col = df_screen.columns[0], df_screen.columns[1]
        so_col = next((c for c in df_screen.columns if any(k in c.lower() for k in ["screen-out", "disqualify"])), None)
        logic_df = df_screen.dropna(subset=[a_col])

        # --- STEP 3: MAPPING ---
        st.header("⚙️ Step 1: Mapping")
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
        if st.button("➕ Add Pair"): 
            st.session_state.consistency_pairs += 1
            st.rerun()

        st.divider()

        # --- STEP 5: RUN AUDIT ---
        if st.button("🚀 Run Categorized Audit"):
            # A. GRADING (Including the new Caution logic)
            def grade_row(row):
                # New Logic: Reject if Caution column is NOT empty
                if 'Caution' in row and pd.notna(row['Caution']) and str(row['Caution']).strip() != "":
                    return pd.Series(["Rejected", "Flagged Caution Note"])
                
                for q, bad_vals in final_rules.items():
                    if not bad_vals: continue
                    col = mapping.get(q)
                    ans = str(row.get(col)).strip().lower() if pd.notna(row.get(col)) else ""
                    if ans in bad_vals: return pd.Series(["Rejected", f"Failed: {q}"])
                return pd.Series(["Qualified", "Pass"])
            
            df_resp[['Status', 'Reason']] = df_resp.apply(grade_row, axis=1)
            df_resp['Suspicion_Score'] = 0
            df_resp['Suspicion_Reasons'] = ""

            # B. FRAUD SCORING
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

            # C. FUZZY GROUPING
            q_cols_mapped = list(set(mapping.values()))
            df_resp['Pattern'] = df_resp[q_cols_mapped].astype(str).agg(' '.join, axis=1)
            fuzzy_groups = []
            already_processed = set()
            for i in range(len(df_resp)):
                if i in already_processed: continue
                p_i = df_resp.iloc[i]['Pattern']
                current_group = [i]
                for j in range(i+1, len(df_resp)):
                    if fuzz.ratio(p_i, df_resp.iloc[j]['Pattern']) >= fuzzy_threshold:
                        current_group.append(j)
                        already_processed.add(j)
                if len(current_group) > 1:
                    fuzzy_groups.append(current_group)
                    for idx in current_group:
                        df_resp.at[df_resp.index[idx], 'Suspicion_Score'] += 6
                        df_resp.at[df_resp.index[idx], 'Suspicion_Reasons'] += f"Pattern Match; "

            # --- D. CATEGORIZED REVIEW ---
            st.header("📊 Audit Dashboard")
            t1, t2, t3, t4 = st.tabs(["👥 Pattern Twins", "📱 Phone Clusters", "⚖️ Consistency", "📥 Full Report"])

            with t1:
                if fuzzy_groups:
                    for g in fuzzy_groups:
                        with st.expander(f"🚩 Group of {len(g)} near-identical responses"):
                            st.table(df_resp.iloc[g][display_cols])
                else: st.success("No fuzzy patterns found.")

            with t2:
                if 'Mobile' in df_resp.columns and not phone_dupes.empty:
                    for prefix, group in phone_dupes.groupby('P_Clean'):
                        with st.expander(f"🚩 {prefix}XXXX ({len(group)} hits)"):
                            # Combine Mobile with standard display cols
                            st.table(group[display_cols + ['Mobile']])
                else: st.success("No phone clusters.")

            with t3:
                has_mismatch = False
                for ca, cb in consistency_rules:
                    mismatches = df_resp[df_resp[ca].apply(normalize) != df_resp[cb].apply(normalize)]
                    if not mismatches.empty:
                        has_mismatch = True
                        with st.expander(f"🚩 Mismatch: {ca} vs {cb}"):
                            st.table(mismatches[display_cols + [ca, cb]])
                if not has_mismatch: st.success("All consistent.")

            with t4:
                st.dataframe(df_resp.sort_values(by='Suspicion_Score', ascending=False))
                st.download_button("📥 Download Final Audit", df_resp.to_csv(index=False).encode('utf-8-sig'), "pfr_audit.csv")

    except Exception as e:
        st.error(f"Audit Error: {e}")
