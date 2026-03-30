import streamlit as st
import pandas as pd
import io
import re
import os
import datetime
from fpdf import FPDF

def get_clean_value_counts(series):
    """Splits delimited strings (like ;) and returns cleaned value counts."""
    s = series.dropna().astype(str)
    if s.str.contains(';').any():
        s = s.str.split(';').explode()
        
    s = s.str.strip()
    
    def clean_format(val):
        if not val:
            return val
        # Remove 'other -' (case insensitive) from the start of the string
        val = re.sub(r'(?i)^other\s*-\s*', '', val).strip()
        # Capitalize only the first letter, preserving the rest of the casing (e.g. acronyms)
        if len(val) > 0:
            val = val[0].upper() + val[1:]
        return val
        
    s = s.apply(clean_format)
    s = s[s != ''] # Remove trailing empties
    return s.value_counts()

def create_pdf_report(df, report_cols, project_name):
    """Generates a branded PDF summary with Unicode character handling."""
    
    def clean_unicode(text):
        """Replaces common decorative unicode characters with ASCII equivalents."""
        if not isinstance(text, str):
            return str(text)
        replacements = {
            '\u2018': "'", '\u2019': "'",  # Smart quotes (left/right)
            '\u201c': '"', '\u201d': '"',  # Smart double quotes
            '\u2013': '-', '\u2014': '-',  # En/Em dashes
            '\u2026': '...',               # Ellipsis
            '\xa0': ' '                     # Non-breaking space
        }
        for bad, good in replacements.items():
            text = text.replace(bad, good)
        # Encode to latin-1 and ignore anything else we missed to prevent crashes
        return text.encode('latin-1', 'ignore').decode('latin-1')

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    # 1. Header & Logo
    if os.path.exists("PFRLogo.png"):
        pdf.image("PFRLogo.png", x=10, y=8, w=40)
        pdf.ln(20)
    
    # 2. Title Section
    pdf.set_font("Arial", 'B', 22)
    pdf.set_text_color(45, 49, 66)
    pdf.cell(0, 15, clean_unicode("Project Summary Report"), ln=True, align='L')
    
    pdf.set_font("Arial", '', 12)
    pdf.set_text_color(79, 93, 117)
    pdf.cell(0, 8, clean_unicode(f"Project: {project_name}"), ln=True)
    pdf.cell(0, 8, f"Date: {datetime.date.today().strftime('%d %B %Y')}", ln=True)
    pdf.cell(0, 8, f"Total Sample: {len(df)} Respondents", ln=True)
    pdf.ln(10)
    
    # 3. Data Tables
    for col in report_cols:
        if pdf.get_y() > 230:
            pdf.add_page()

        pdf.set_font("Arial", 'B', 12)
        pdf.set_fill_color(79, 93, 117)
        pdf.set_text_color(255, 255, 255)
        # Clean the column header
        pdf.cell(0, 10, clean_unicode(f"  Metric: {col}"), ln=True, fill=True)
        
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Arial", 'B', 10)
        pdf.set_fill_color(240, 240, 240)
        pdf.cell(110, 8, " Response Option", border=1, fill=True)
        pdf.cell(35, 8, " Count", border=1, fill=True)
        pdf.cell(35, 8, " Percentage", border=1, fill=True, ln=True)
        
        pdf.set_font("Arial", '', 10)
        stats = get_clean_value_counts(df[col])
        total_n = stats.sum()
        
        for label, count in stats.items():
            # Clean the data labels
            display_label = clean_unicode(str(label))
            clean_label = display_label[:55] + ('...' if len(display_label) > 55 else '')
            
            pdf.cell(110, 7, f" {clean_label}", border=1)
            pdf.cell(35, 7, f" {count}", border=1)
            pdf.cell(35, 7, f" {(count/total_n)*100:.1f}%", border=1, ln=True)
        
        pdf.ln(8)
        
    # Return as bytes
    return pdf.output(dest='S').encode('latin-1')

# --- 1. CONFIG & SETTINGS ---
st.set_page_config(page_title="PFR Client Reporting Tool", layout="wide")

# Custom CSS
st.markdown("""
<style>
span[data-baseweb="tag"] {
    background-color: #4F5D75 !important;
}
</style>
""", unsafe_allow_html=True)

# --- 2. APP INTERFACE ---
st.title("📊 PFR Client Report Generator")
st.markdown("Convert call lists into anonymised, client-ready Excel & PDF reports.")

uploaded_file = st.file_uploader("Upload Audited CSV/Excel", type=["csv", "xlsx"])

if uploaded_file:
    # Load Data
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
        
    # --- 3. PII STRIPPING SETTINGS ---
    st.sidebar.header("🛡️ Privacy Settings")
    default_pii = [c for c in headers if any(k in c.lower() for k in ['phone', 'tel', 'email', 'name', 'mobile', 'address', 'postcode', 'ip'])]
    pii_to_strip = st.sidebar.multiselect("Columns to Strip:", headers, default=default_pii)
    
    default_id_index = 0
    for i, col in enumerate(headers):
        if col.strip().lower() == 'participant id':
            default_id_index = i
            break
            
    id_col = st.sidebar.selectbox("Anchor ID Column (Keep):", headers, index=default_id_index)

    # --- REPORT GRAPH SETTINGS ---
    st.sidebar.divider()
    st.sidebar.header("📊 Report Settings")
    q_columns = [c for c in headers if c.strip().upper().startswith('Q') or c.strip().upper() == 'STATUS']
    valid_graph_cols = [c for c in q_columns if c not in pii_to_strip]
    
    report_graph_cols = st.sidebar.multiselect(
        "Columns for Summary:",
        options=valid_graph_cols,
        default=valid_graph_cols
    )

    # --- 4. DATA VISUALIZATION (LIVE PREVIEW) ---
    st.header("🔍 Report Preview")
    tab1, tab2 = st.tabs(["📈 Data Distributions", "📋 Anonymized Preview"])

    with tab1:
        col_left, col_right = st.columns(2)
        vis_options = [c for c in valid_graph_cols if c != id_col]
        
        if vis_options:
            selected_vis = col_left.selectbox("Select Research Metric:", vis_options)
            chart_data = get_clean_value_counts(df[selected_vis]).reset_index()
            chart_data.columns = ['Metric', 'Count']
            
            with col_left.expander(f"📊 {selected_vis} Chart", expanded=True):
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

    # --- 5. EXPORT SECTION ---
    st.divider()
    st.subheader("📦 Generate Downloads")
    col_dl1, col_dl2 = st.columns(2)

    clean_project_name = uploaded_file.name.split('.')[0].replace('_', ' ').title()

    with col_dl1:
        if st.button("Generate Excel Report"):
            try:
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    workbook = writer.book
                    
                    # --- SHEET 1: PROJECT OVERVIEW ---
                    title_page = workbook.add_worksheet('Project Overview')
                    title_page.hide_gridlines(2)
                    main_title_fmt = workbook.add_format({'bold': True, 'font_size': 26, 'font_color': '#2D3142', 'align': 'center', 'valign': 'vcenter'})
                    meta_fmt = workbook.add_format({'font_size': 12, 'font_color': '#4F5D75', 'align': 'center'})
                    accent_line_fmt = workbook.add_format({'bg_color': '#EF8354'}) 

                    if os.path.exists('PFRLogo.png'):
                        title_page.insert_image('B4', 'PFRLogo.png', {'x_scale': 1, 'y_scale': 1, 'x_offset': 40})

                    title_page.merge_range('A12:I13', clean_project_name, main_title_fmt)
                    today_str = datetime.date.today().strftime("%d %B %Y")
                    title_page.merge_range('A15:I15', f"Created: {today_str}", meta_fmt)
                    title_page.merge_range('A16:I16', f"Total Sample Size: {len(df)} Respondents", meta_fmt)
                    title_page.set_row(18, 2) 
                    title_page.merge_range('C19:G19', '', accent_line_fmt)

                    # --- SHEET 2: SUMMARY ---
                    summary_sheet = workbook.add_worksheet('Summary')
                    summary_sheet.hide_gridlines(2)
                    summary_header_fmt = workbook.add_format({'bold': True, 'font_size': 16, 'font_color': '#FFFFFF', 'bg_color': '#2D3142', 'align': 'center', 'valign': 'vcenter'})
                    stat_header_fmt = workbook.add_format({'bold': True, 'border': 1, 'font_size': 12, 'text_wrap': True, 'valign': 'top', 'bg_color': '#4F5D75', 'font_color': '#FFFFFF'})
                    
                    summary_sheet.merge_range('A1:H3', f"{clean_project_name.upper()} - METRICS", summary_header_fmt)
                    if os.path.exists('PFRLogo.png'):
                        summary_sheet.insert_image('A1', 'PFRLogo.png', {'x_scale': 0.50, 'y_scale': 0.50, 'x_offset': 10, 'y_offset': 4})
                    
                    summary_sheet.set_column('B:B', 40)
                    summary_sheet.set_column('C:C', 12)
                    summary_sheet.set_column('D:D', 12, workbook.add_format({'num_format': '0.0%'}))

                    current_row = 4
                    for col_name in report_graph_cols:
                        summary_sheet.write(current_row, 0, f"{col_name}", stat_header_fmt)
                        summary_sheet.write(current_row, 1, "Count", stat_header_fmt)
                        summary_sheet.write(current_row, 2, "Percent", stat_header_fmt)
                        
                        stats_series = get_clean_value_counts(df[col_name])
                        if len(stats_series) == 0: continue
                        
                        stats_df = pd.DataFrame({'Count': stats_series, 'Percentage': stats_series / stats_series.sum()})
                        
                        border_fmt = workbook.add_format({'border': 1, 'align': 'left'})
                        border_pct_fmt = workbook.add_format({'border': 1, 'num_format': '0.0%', 'align': 'right'})
                        
                        for idx, (label, row_data) in enumerate(stats_df.iterrows()):
                            r = current_row + 1 + idx
                            summary_sheet.write(r, 0, label, border_fmt)
                            summary_sheet.write(r, 1, row_data['Count'], border_fmt)
                            summary_sheet.write(r, 2, row_data['Percentage'], border_pct_fmt)
                        
                        # Charts
                        dynamic_height = max(350, len(stats_df) * 45)
                        dynamic_width = max(550, len(stats_df) * 50)
                        
                        chart1 = workbook.add_chart({'type': 'bar'})
                        chart1.set_size({'width': dynamic_width, 'height': dynamic_height})
                        chart1.add_series({
                            'name': 'Counts',
                            'categories': ['Summary', current_row + 1, 0, current_row + len(stats_df), 0],
                            'values': ['Summary', current_row + 1, 1, current_row + len(stats_df), 1],
                            'fill': {'color': '#EF8354'},
                            'gap': 80
                        })
                        summary_sheet.insert_chart(current_row, 4, chart1)
                        current_row += max(len(stats_df) + 4, int(dynamic_height / 20) + 2)

                    # --- SHEET 3: DATA ---
                    display_df.to_excel(writer, sheet_name='Anonymized Data', index=False)
                    data_sheet = writer.sheets['Anonymized Data']
                    data_sheet.autofilter(0, 0, 0, len(display_df.columns) - 1)
                    data_sheet.set_column(0, len(display_df.columns) - 1, 18)

                st.download_button(
                    label="📥 Download Excel (.xlsx)",
                    data=output.getvalue(),
                    file_name=f"PFR_Report_{uploaded_file.name.split('.')[0]}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except Exception as e:
                st.error(f"Excel Error: {e}")

    with col_dl2:
        if st.button("Generate PDF Summary"):
            try:
                pdf_bytes = create_pdf_report(df, report_graph_cols, clean_project_name)
                st.download_button(
                    label="📄 Download PDF Summary",
                    data=pdf_bytes,
                    file_name=f"PFR_Summary_{uploaded_file.name.split('.')[0]}.pdf",
                    mime="application/pdf"
                )
            except Exception as e:
                st.error(f"PDF Error: {e}")

else:
    st.info("👋 Welcome! Please upload a CSV or Excel file to generate the client report.")
