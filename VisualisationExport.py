import streamlit as st
import pandas as pd
import io
import re
import os
import datetime
import tempfile  # New import for the fix
from fpdf import FPDF
import matplotlib.pyplot as plt

# --- HELPER FUNCTIONS ---
def get_clean_value_counts(series):
    """Splits delimited strings (like ;) and returns cleaned value counts."""
    s = series.dropna().astype(str)
    if s.str.contains(';').any():
        s = s.str.split(';').explode()
    s = s.str.strip()

    def clean_format(val):
        if not val:
            return val
        val = re.sub(r'(?i)^other\s*-\s*', '', val).strip()
        if len(val) > 0:
            val = val[0].upper() + val[1:]
        return val

    s = s.apply(clean_format)
    s = s[s != '']
    return s.value_counts()

# --- PDF GENERATOR ---
def create_pdf_report(df, report_cols, project_name, mode):
    """Generates a PDF summary with optional Tables and/or Matplotlib Graphs."""
    def clean_unicode(text):
        if not isinstance(text, str):
            return str(text)
        replacements = {
            '\u2018': "'", '\u2019': "'",
            '\u201c': '"', '\u201d': '"',
            '\u2013': '-', '\u2014': '-',
            '\u2026': '...', '\xa0': ' '
        }
        for bad, good in replacements.items():
            text = text.replace(bad, good)
        return text.encode('latin-1', 'ignore').decode('latin-1')

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # --- TITLE PAGE ---
    pdf.add_page()
    if os.path.exists("PFRLogo.png"):
        pdf.image("PFRLogo.png", x=10, y=8, w=40)
        pdf.ln(20)

    pdf.set_font("Arial", 'B', 22)
    pdf.set_text_color(45, 49, 66)
    pdf.cell(0, 15, clean_unicode("Project Summary Report"), ln=True)

    pdf.set_font("Arial", '', 12)
    pdf.set_text_color(79, 93, 117)
    pdf.cell(0, 8, clean_unicode(f"Project: {project_name}"), ln=True)
    pdf.cell(0, 8, f"Date: {datetime.date.today().strftime('%d %B %Y')}", ln=True)
    pdf.cell(0, 8, f"Total Sample: {len(df)} Respondents", ln=True)

    # --- CONTENT ---
    for col in report_cols:
        pdf.add_page()

        pdf.set_font("Arial", 'B', 12)
        pdf.set_fill_color(79, 93, 117)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 10, clean_unicode(f" Metric: {col}"), ln=True, fill=True)

        stats = get_clean_value_counts(df[col])
        if stats.empty:
            continue
        total_n = stats.sum()

        # --- TABLES ---
        if "Tables" in mode:
            pdf.ln(5)
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Arial", 'B', 10)
            pdf.set_fill_color(240, 240, 240)

            pdf.cell(110, 8, " Response Option", border=1, fill=True)
            pdf.cell(35, 8, " Count", border=1, fill=True)
            pdf.cell(35, 8, " Percentage", border=1, fill=True, ln=True)

            pdf.set_font("Arial", '', 10)
            for label, count in stats.items():
                clean_label = clean_unicode(str(label))
                clean_label = clean_label[:55] + ('...' if len(clean_label) > 55 else '')
                pdf.cell(110, 7, f" {clean_label}", border=1)
                pdf.cell(35, 7, f" {count}", border=1)
                pdf.cell(35, 7, f" {(count/total_n)*100:.1f}%", border=1, ln=True)

        # --- GRAPHS (FIXED FOR STARTSSWITH ERROR) ---
        if "Graphs" in mode:
            fig, ax = plt.subplots(figsize=(8, 4))
            labels = [clean_unicode(str(l))[:30] for l in stats.index]
            ax.barh(labels, stats.values, color='#EF8354')
            ax.invert_yaxis()
            ax.set_title(f"Distribution: {col}", fontsize=10)
            plt.tight_layout()

            # Fix: Save to a real temporary file so FPDF can process the path string
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                plt.savefig(tmp.name, format='png', dpi=150)
                tmp_path = tmp.name
            
            pdf.ln(5)
            pdf.image(tmp_path, x=15, w=180)
            plt.close(fig)
            
            # Clean up the temporary file immediately
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    return pdf.output(dest='S').encode('latin-1')


# --- STREAMLIT CONFIG ---
st.set_page_config(page_title="PFR Client Reporting Tool", layout="wide")

st.markdown("""
<style>
span[data-baseweb="tag"] {
    background-color: #4F5D75 !important;
}
</style>
""", unsafe_allow_html=True)

# --- UI ---
st.title("📊 PFR Client Report Generator")
st.markdown("Convert call lists into anonymised, client-ready Excel & PDF reports.")

uploaded_file = st.file_uploader("Upload Audited CSV/Excel", type=["csv", "xlsx"])

if uploaded_file:

    if uploaded_file.name.endswith('.csv'):
        df = pd.read_csv(uploaded_file, encoding='utf-8-sig')
    else:
        df = pd.read_excel(uploaded_file)

    status_map = {
        14: 'Not Suitable', '14': 'Not Suitable',
        19: 'Applied', '19': 'Applied',
        23: 'Invited', '23': 'Invited',
        30: 'Completed Task', '30': 'Completed Task',
        33: 'Suitable', '33': 'Suitable'
    }
    if 'Status' in df.columns:
        df['Status'] = df['Status'].replace(status_map)

    headers = df.columns.tolist()

    if os.path.exists("PFRLogo.png"):
        st.sidebar.image("PFRLogo.png", width=250)

    st.sidebar.header("🛡️ Privacy Settings")
    default_pii = [c for c in headers if any(k in c.lower() for k in ['phone','tel','email','name','mobile','address','postcode','ip'])]
    pii_to_strip = st.sidebar.multiselect("Columns to Strip:", headers, default=default_pii)

    default_id_index = 0
    for i, col in enumerate(headers):
        if col.strip().lower() == 'participant id':
            default_id_index = i
            break
    id_col = st.sidebar.selectbox("Anchor ID Column (Keep):", headers, index=default_id_index)

    st.sidebar.divider()
    st.sidebar.header("📄 PDF Options")
    pdf_mode = st.sidebar.radio("PDF Content:", ["Tables Only", "Graphs Only", "Tables & Graphs"], index=2)

    st.sidebar.divider()
    st.sidebar.header("📊 Report Settings")

    q_columns = [c for c in headers if c.strip().upper().startswith('Q') or c.strip().upper() == 'STATUS']
    valid_graph_cols = [c for c in q_columns if c not in pii_to_strip]

    report_graph_cols = st.sidebar.multiselect(
        "Columns for Summary:",
        options=valid_graph_cols,
        default=valid_graph_cols
    )

    st.header("🔍 Report Preview")
    tab1, tab2 = st.tabs(["📈 Data Distributions", "📋 Anonymized Preview"])

    with tab1:
        col_left, col_right = st.columns(2)
        if valid_graph_cols:
            selected_vis = col_left.selectbox("Select Research Metric:", valid_graph_cols)
            chart_data = get_clean_value_counts(df[selected_vis]).reset_index()
            chart_data.columns = ['Metric', 'Count']
            with col_left.expander(f"📊 {selected_vis}", expanded=True):
                st.bar_chart(chart_data.set_index('Metric'), color="#EF8354")

            if 'Status' in df.columns:
                with col_right.expander("📋 Qualification Overview", expanded=True):
                    status_data = get_clean_value_counts(df['Status']).reset_index()
                    status_data.columns = ['Status_Label', 'Count']
                    st.bar_chart(status_data.set_index('Status_Label'), color="#EF8354")

    with tab2:
        display_df = df.drop(columns=pii_to_strip, errors='ignore')
        st.dataframe(display_df.head(50))
        st.caption(f"Showing first 50 rows. Total rows: {len(df)}")

    st.divider()
    st.subheader("📦 Generate Downloads")

    col_dl1, col_dl2 = st.columns(2)
    clean_project_name = uploaded_file.name.split('.')[0].replace('_', ' ').title()

    # --- EXCEL ---
    with col_dl1:
        if st.button("Generate Excel Report"):
            try:
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    workbook = writer.book

                    title_page = workbook.add_worksheet('Project Overview')
                    title_page.hide_gridlines(2)
                    main_title_fmt = workbook.add_format({'bold': True,'font_size': 26,'font_color': '#2D3142','align': 'center','valign': 'vcenter'})
                    meta_fmt = workbook.add_format({'font_size': 12,'font_color': '#4F5D75','align': 'center'})
                    accent_line_fmt = workbook.add_format({'bg_color': '#EF8354'})

                    if os.path.exists('PFRLogo.png'):
                        title_page.insert_image('B4', 'PFRLogo.png')

                    title_page.merge_range('A12:I13', clean_project_name, main_title_fmt)
                    today_str = datetime.date.today().strftime("%d %B %Y")
                    title_page.merge_range('A15:I15', f"Created: {today_str}", meta_fmt)
                    title_page.merge_range('A16:I16', f"Total Sample Size: {len(df)} Respondents", meta_fmt)
                    title_page.merge_range('C19:G19', '', accent_line_fmt)

                    summary_sheet = workbook.add_worksheet('Summary')
                    summary_sheet.hide_gridlines(2)

                    current_row = 4
                    for col_name in report_graph_cols:
                        stats_series = get_clean_value_counts(df[col_name])
                        if stats_series.empty:
                            continue

                        stats_df = pd.DataFrame({'Count': stats_series, 'Percentage': stats_series / stats_series.sum()})

                        summary_sheet.write(current_row - 1, 0, col_name, workbook.add_format({'bold': True}))
                        for idx, (label, row_data) in enumerate(stats_df.iterrows()):
                            r = current_row + idx
                            summary_sheet.write(r, 0, label)
                            summary_sheet.write(r, 1, row_data['Count'])
                            summary_sheet.write(r, 2, row_data['Percentage'], workbook.add_format({'num_format': '0.0%'}))

                        chart = workbook.add_chart({'type': 'bar'})
                        chart.add_series({
                            'categories': ['Summary', current_row, 0, current_row + len(stats_df)-1, 0],
                            'values': ['Summary', current_row, 1, current_row + len(stats_df)-1, 1],
                        })
                        summary_sheet.insert_chart(current_row, 4, chart)
                        current_row += len(stats_df) + 15

                    display_df.to_excel(writer, sheet_name='Anonymized Data', index=False)

                st.download_button("📥 Download Excel", data=output.getvalue(), file_name=f"PFR_Report_{clean_project_name}.xlsx")
            except Exception as e:
                st.error(f"Excel Error: {e}")

    # --- PDF ---
    with col_dl2:
        if st.button("Generate PDF Summary"):
            try:
                with st.spinner("Generating PDF..."):
                    pdf_bytes = create_pdf_report(df, report_graph_cols, clean_project_name, pdf_mode)

                st.download_button("📄 Download PDF", data=pdf_bytes, file_name=f"PFR_Summary_{clean_project_name}.pdf")
            except Exception as e:
                st.error(f"PDF Error: {e}")

else:
    st.info("👋 Upload a file to generate your report.")
