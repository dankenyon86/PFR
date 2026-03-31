import streamlit as st
import pandas as pd
import io
import re
import os
import datetime
import tempfile
from fpdf import FPDF
import matplotlib.pyplot as plt
import numpy as np

# --- 0. BRANDING & FONTS ---
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Lexend:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"], .stText, .stMarkdown, p, span, label, h1, h2, h3, h4, h5, h6 {
    font-family: 'Lexend', sans-serif !important;
}

/* Customizing the sidebar tags to match your theme */
span[data-baseweb="tag"] { 
    background-color: #4F5D75 !important; 
    font-family: 'Lexend', sans-serif !important;
}
</style>
""", unsafe_allow_html=True)

# --- 1. CORE UTILITIES ---
def get_clean_value_counts(series, sort_numerically=False):
    """Splits delimited strings and returns cleaned counts, optionally sorted numerically."""
    s = series.dropna().astype(str)
    if s.empty:
        return pd.Series(dtype=int)
        
    if s.str.contains(';').any():
        s = s.str.split(';').explode()
    s = s.str.strip()
    
    def clean_format(val):
        if not val or val.lower() == 'nan': return ""
        val = re.sub(r'(?i)^other\s*-\s*', '', val).strip()
        return val[0].upper() + val[1:] if len(val) > 0 else val
        
    s = s.apply(clean_format)
    s = s[s != ''] 
    counts = s.value_counts()

    if sort_numerically and not counts.empty:
        try:
            def extract_num(text):
                match = re.search(r'(\d+)', str(text))
                return int(match.group(1)) if match else 999999
            
            sorted_labels = sorted(counts.index, key=extract_num)
            counts = counts.reindex(sorted_labels)
        except Exception as e:
            pass
            
    return counts

def is_continuous_data(series, col_name):
    """Detects if a column should be treated as continuous (Histogram)."""
    name_low = col_name.lower()
    if any(k in name_low for k in ["age", "income", "salary", "height", "weight", "years", "spend", "cost"]):
        return True
    try:
        numeric_check = pd.to_numeric(series, errors='coerce')
        if numeric_check.notnull().mean() > 0.8:
            return True
    except:
        pass
    return False

# --- 2. PDF GENERATION ENGINE ---
def create_pdf_report(df, report_cols, project_name, mode):
    pdf = FPDF()
    try:
        pdf.add_font('Lexend', '', 'Lexend-Regular.ttf', uni=True)
        pdf.add_font('Lexend', 'B', 'Lexend-Bold.ttf', uni=True)
        font_name = "Lexend"
    except:
        font_name = "Arial"

    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font(font_name, 'B', 22)
    pdf.cell(0, 15, "Project Summary Report", ln=True)

    # --- TITLE PAGE ---
    pdf.add_page()
    if os.path.exists("PFRLogo.png"):
        pdf.image("PFRLogo.png", x=10, y=10, w=100) 
        pdf.ln(50) 
    else:
        pdf.ln(20)

    pdf.set_font("Calibri", 'B', 22)
    pdf.set_text_color(45, 49, 66)
    pdf.cell(0, 15, clean_unicode("Project Summary Report"), ln=True)

    pdf.set_font("Arial", '', 12)
    pdf.set_text_color(79, 93, 117)
    pdf.cell(0, 8, clean_unicode(f"Project: {project_name}"), ln=True)
    pdf.cell(0, 8, f"Date: {datetime.date.today().strftime('%d %B %Y')}", ln=True)
    pdf.cell(0, 8, f"Total Sample: {len(df)} Respondents", ln=True)

    # --- CONTENT PAGES ---
    for col in report_cols:
        pdf.add_page()
        pdf.set_font("Arial", 'B', 12)
        pdf.set_fill_color(79, 93, 117)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(0, 10, clean_unicode(f" Metric: {col}"), ln=True, fill=True)

        is_cont = is_continuous_data(df[col], col)
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

        # --- GRAPHS ---
        if "Graphs" in mode:
            fig, ax = plt.subplots(figsize=(8, 4))
            is_cont = is_continuous_data(df[col], col)
            stats = get_clean_value_counts(df[col], sort_numerically=is_cont)
            
            if is_cont:
                ax.bar(stats.index.astype(str), stats.values, color='#EF8354', width=1.0, edgecolor='white')
                ax.set_title(f"Numerical Distribution: {col}", fontsize=10)
                plt.xticks(rotation=45)
            else:
                labels = [clean_unicode(str(l))[:30] for l in stats.index]
                ax.barh(labels, stats.values, color='#EF8354')
                ax.invert_yaxis()
                ax.set_title(f"Categorical Breakdown: {col}", fontsize=10)
            
            plt.tight_layout()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                plt.savefig(tmp.name, format='png', dpi=150)
                tmp_path = tmp.name
            
            pdf.ln(5)
            pdf.image(tmp_path, x=15, w=180)
            plt.close(fig)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    return pdf.output(dest='S').encode('latin-1')

# --- 3. STREAMLIT UI & INTERFACE ---
st.set_page_config(page_title="PFR Client Reporting Tool", layout="wide")

st.markdown("""
<style>
span[data-baseweb="tag"] { background-color: #4F5D75 !important; }
</style>
""", unsafe_allow_html=True)

st.markdown("""<style>@import url('https://fonts.googleapis.com/css2?family=Lexend:wght@300;400;700&display=swap'); html, body, [class*="css"]  {font-family: 'Lexend', sans-serif;}</style>""", unsafe_allow_html=True)

if os.path.exists("PFRLogo.png"):
    st.sidebar.image("PFRLogo.png", width=350)

st.title("PFR Client Report Generator")
st.markdown("Convert call lists into anonymised, client-ready Excel & PDF reports.")

uploaded_file = st.file_uploader("Upload Call List", type=["csv", "xlsx"])

if not uploaded_file:
    st.info("Welcome! Please upload a call list to generate the client report.")
    st.stop()

# --- 4. DATA PROCESSING ---
if uploaded_file.name.endswith('.csv'):
    df = pd.read_csv(uploaded_file, encoding='utf-8-sig')
else:
    df = pd.read_excel(uploaded_file)

# Qualification Status Mapping
status_map = {
1: 'Applied', '1': 'Applied',
12: 'Unsuccessful Contact', '12': 'Unsuccessful Contact',
13: 'Waiting For Client', '13': 'Waiting For Client',
14: 'Not Suitable', '14': 'Not Suitable',
15: 'Confirmed', '15': 'Confirmed',
16: 'Cancelled', '16': 'Cancelled',
17: 'Attended', '17': 'Attended',
18: 'No Show', '18': 'No Show',
19: 'Stand By', '19': 'Stand By',
20: 'Dropout', '20': 'Dropout',
21: 'Misrecruit', '21': 'Misrecruit',
22: 'Suitable Invite', '22': 'Suitable Invite',
23: 'Invited', '23': 'Invited',
24: 'Email Reminder', '24': 'Email Reminder',
25: 'Text Reminder', '25': 'Text Reminder',
26: 'Past Deadline', '26': 'Past Deadline',
27: 'Technical Issue Incomplete', '27': 'Technical Issue Incomplete',
28: 'Misrecruit Completed', '28': 'Misrecruit Completed',
29: 'Misrecruit Incomplete', '29': 'Misrecruit Incomplete',
30: 'Completed Task', '30': 'Completed Task',
31: 'Incomplete Task', '31': 'Incomplete Task',
32: 'Didnt Follow Instructions', '32': 'Didnt Follow Instructions',
33: 'Suitable', '33': 'Suitable',
34: 'Fake', '34': 'Fake',
35: 'Scheduled', '35': 'Scheduled',
36: 'Screening', '36': 'Screening'
}
if 'Status' in df.columns:
    df['Status'] = df['Status'].replace(status_map)

headers = df.columns.tolist()

# Sidebar Settings
st.sidebar.header("Privacy Settings")
default_pii = [c for c in headers if any(k in c.lower() for k in ['phone', 'tel', 'email', 'name', 'mobile', 'address', 'postcode', 'ip'])]
pii_to_strip = st.sidebar.multiselect("Columns to Strip (PII):", headers, default=default_pii)

default_id_index = 0
for i, col in enumerate(headers):
    if col.strip().lower() == 'participant id':
        default_id_index = i
        break
id_col = st.sidebar.selectbox("Anchor ID Column (Keep):", headers, index=default_id_index)

st.sidebar.divider()
st.sidebar.header("PDF Export Options")
q_columns = [c for c in headers]
valid_graph_cols = [c for c in q_columns if c not in pii_to_strip]
pdf_mode = st.sidebar.radio("PDF Content:", ["Tables Only", "Graphs Only", "Tables & Graphs"], index=2)

st.sidebar.divider()
st.sidebar.header("Excel Report Settings")
report_graph_cols = st.sidebar.multiselect("Columns for Visualisation:", options=valid_graph_cols, default=valid_graph_cols)

# --- 5. VISUALIZATION PREVIEW (TABBED) ---
tab1, tab2 = st.tabs(["Live Distributions", "Anonymized Preview"])

with tab1:
    col_l, col_r = st.columns(2)
    vis_opts = [c for c in valid_graph_cols if c != id_col]
    if vis_opts:
        selected_vis = col_l.selectbox("Select Research Metric:", vis_opts)
        is_cont = is_continuous_data(df[selected_vis], selected_vis)
        chart_data = get_clean_value_counts(df[selected_vis], sort_numerically=is_cont).reset_index()
        chart_data.columns = ['Metric', 'Count']
        with col_l.expander(f"📊 {selected_vis} Preview", expanded=True):
            st.bar_chart(chart_data.set_index('Metric'), color="#EF8354")
    
    if 'Status' in df.columns:
        with col_r.expander("📋 Qualification Overview", expanded=True):
            status_data = get_clean_value_counts(df['Status']).reset_index()
            status_data.columns = ['Status_Label', 'Count']
            st.bar_chart(status_data.set_index('Status_Label'), color="#4F5D75")

with tab2:
    display_df = df.drop(columns=pii_to_strip, errors='ignore')
    st.dataframe(display_df.head(50))
    st.caption(f"Showing first 50 rows. Total rows: {len(df)}")

# --- 6. EXPORT GENERATION ENGINE ---
st.divider()
st.subheader("Generate Downloads")
col_dl1, col_dl2 = st.columns(2)
clean_project_name = uploaded_file.name.split('.')[0].replace('_', ' ').title()

# --- EXCEL EXPORT ---
with col_dl1:
    if st.button("Generate Excel Report"):
        try:
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                workbook = writer.book
                
                # --- SHEET 1: PROJECT OVERVIEW ---
                title_page = workbook.add_worksheet('Project Overview')
                title_page.hide_gridlines(2)
                
                # Formats for a professional look
                main_title_fmt = workbook.add_format({'font_name': 'Lexend', 'bold': True, 'font_size': 26, 'font_color': '#2D3142', 'align': 'center'})
                meta_fmt = workbook.add_format({'font_name': 'Lexend', 'font_size': 12, 'font_color': '#4F5D75', 'align': 'center'})
                accent_line_fmt = workbook.add_format({'bg_color': '#EF8354'}) 

                # 1. Place Logo at the very top of the first page
                if os.path.exists('PFRLogo.png'):
                    title_page.insert_image('A1', 'PFRLogo.png', {'x_scale': 0.075, 'y_scale': 0.075})

                # 2. Project Info - Start at row 14 to leave clear space for the logo
                title_page.merge_range('A14:I15', clean_project_name, main_title_fmt)
                today_str = datetime.date.today().strftime("%d %B %Y")
                title_page.merge_range('A17:I17', f"Created Date: {today_str}", meta_fmt)
                title_page.merge_range('A18:I18', f"Total Sample Size: {len(df)} Respondents", meta_fmt)
                
                # Visual accent line
                title_page.set_row(19, 3) 
                title_page.merge_range('C20:G20', '', accent_line_fmt)

                # --- SHEET 2: SUMMARY & CHARTS ---
                summary_sheet = workbook.add_worksheet('Summary')
                summary_sheet.hide_gridlines(2)
                summary_header_fmt = workbook.add_format({'bold': True, 'font_size': 16, 'font_color': '#FFFFFF', 'bg_color': '#2D3142', 'align': 'center', 'valign': 'vcenter'})
                stat_header_fmt = workbook.add_format({'bold': True, 'border': 1, 'font_size': 11, 'bg_color': '#4F5D75', 'font_color': '#FFFFFF', 'align': 'center'})
                
                # Header Bar
                summary_sheet.merge_range('A1:L3', f"{clean_project_name.upper()} - INSIGHTS", summary_header_fmt)
                
                # Column widths for readability
                summary_sheet.set_column('A:A', 45)
                summary_sheet.set_column('B:C', 15)

                current_row = 5
                CHART_WIDTH = 550
                CHART_HEIGHT = 380

                for col_name in report_graph_cols:
                    is_cont = is_continuous_data(df[col_name], col_name)
                    stats_series = get_clean_value_counts(df[col_name], sort_numerically=is_cont)
                    
                    if stats_series.empty or len(stats_series) == 0:
                        continue
                    
                    stats_df = pd.DataFrame({
                        'Count': stats_series, 
                        'Percentage': stats_series / stats_series.sum()
                    })
                    num_rows = len(stats_df)

                    summary_sheet.write(current_row, 0, col_name, stat_header_fmt)
                    summary_sheet.write(current_row, 1, "Count", stat_header_fmt)
                    summary_sheet.write(current_row, 2, "Percent", stat_header_fmt)
                    
                    border_fmt = workbook.add_format({'border': 1, 'align': 'left'})
                    border_pct_fmt = workbook.add_format({'border': 1, 'num_format': '0.0%', 'align': 'right'})
                    
                    for idx, (label, row_data) in enumerate(stats_df.iterrows()):
                        r = current_row + 1 + idx
                        summary_sheet.write(r, 0, label, border_fmt)
                        summary_sheet.write(r, 1, row_data['Count'], border_fmt)
                        summary_sheet.write(r, 2, row_data['Percentage'], border_pct_fmt)

                    if num_rows > 0:
                        categories_range = ['Summary', current_row + 1, 0, current_row + num_rows, 0]
                        values_count_range = ['Summary', current_row + 1, 1, current_row + num_rows, 1]
                        values_pct_range = ['Summary', current_row + 1, 2, current_row + num_rows, 2]

                        # --- CHART 1: COUNTS (ORANGE) ---
                        chart1 = workbook.add_chart({'type': 'column' if is_cont else 'bar'})
                        chart1.set_size({'width': CHART_WIDTH, 'height': CHART_HEIGHT})
                        chart1.add_series({
                            'categories': categories_range,
                            'values':     values_count_range,
                            'fill': {'color': '#EF8354'}, 
                            'gap': 20 if is_cont else 60,
                            'name': 'Response Count'
                        })
                        chart1.set_title({'name': f'Volume: {col_name}'})
                        chart1.set_legend({'none': True})
                        if not is_cont: chart1.set_y_axis({'reverse': True})
                        summary_sheet.insert_chart(current_row, 4, chart1)

                        # --- CHART 2: PERCENTAGE (BLUE) ---
                        chart2 = workbook.add_chart({'type': 'column' if is_cont else 'bar'})
                        chart2.set_size({'width': CHART_WIDTH, 'height': CHART_HEIGHT})
                        chart2.add_series({
                            'categories': categories_range,
                            'values':     values_pct_range,
                            'fill': {'color': '#4F5D75'}, 
                            'gap': 20 if is_cont else 60,
                            'name': 'Percentage %'
                        })
                        chart2.set_title({'name': f'Percentage: {col_name}'})
                        chart2.set_legend({'none': True})
                        if not is_cont: chart2.set_y_axis({'reverse': True})
                        
                        summary_sheet.insert_chart(current_row, 14, chart2)
                    
                    current_row += max(num_rows + 5, 25)

                display_df.to_excel(writer, sheet_name='Anonymized Data', index=False)
                data_sheet = writer.sheets['Anonymized Data']
                data_sheet.freeze_panes(1, 0)
                data_sheet.set_column(0, len(display_df.columns) - 1, 20)

            st.download_button("📥 Download Excel Report", output.getvalue(), f"PFR_Report_{clean_project_name}.xlsx")
        except Exception as e:
            st.error(f"Excel Error: {e}")

# --- PDF EXPORT ---
with col_dl2:
    if st.button("Generate PDF Summary"):
        try:
            with st.spinner("Generating PDF Report..."):
                pdf_bytes = create_pdf_report(df, report_graph_cols, clean_project_name, pdf_mode)
                st.download_button("📄 Download PDF Report", pdf_bytes, f"PFR_Summary_{clean_project_name}.pdf")
        except Exception as e:
            st.error(f"PDF Error: {e}")
