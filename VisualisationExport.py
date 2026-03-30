import streamlit as st
import pandas as pd
import io
import re

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

# --- 1. CONFIG & SETTINGS ---
st.set_page_config(page_title="PFR Client Reporting Tool", layout="wide")

# Custom CSS to force modern corporate blue on multiselect chips 
st.markdown("""
<style>
span[data-baseweb="tag"] {
    background-color: #4472C4 !important;
}
</style>
""", unsafe_allow_html=True)


# --- 2. APP INTERFACE ---
st.title("📊 PFR Client Report Generator")
st.markdown("Convert call lists into anonymised, client-ready Excel reports.")

uploaded_file = st.file_uploader("Upload Audited CSV/Excel", type=["csv", "xlsx"])

if uploaded_file:
    # Load Data
    if uploaded_file.name.endswith('.csv'):
        df = pd.read_csv(uploaded_file, encoding='utf-8-sig')
    else:
        df = pd.read_excel(uploaded_file)

    headers = df.columns.tolist()
    
    # --- 3. PII STRIPPING SETTINGS ---
    st.sidebar.header("🛡️ Privacy Settings")
    st.sidebar.info("Select columns to REMOVE from the client report (PII).")
    
    # Auto-detect common PII
    default_pii = [c for c in headers if any(k in c.lower() for k in ['phone', 'tel', 'email', 'name', 'mobile', 'address', 'postcode', 'ip'])]
    pii_to_strip = st.sidebar.multiselect("Columns to Strip:", headers, default=default_pii)
    
    # Identify the ID column to ensure it stays
    default_id_index = 0
    for i, col in enumerate(headers):
        if col.strip().lower() == 'participant id':
            default_id_index = i
            break
            
    id_col = st.sidebar.selectbox("Anchor ID Column (Keep):", headers, index=default_id_index)

    # --- REPORT GRAPH SETTINGS ---
    st.sidebar.divider()
    st.sidebar.header("📊 Excel Report Settings")
    st.sidebar.info("Select columns to visualise in the Summary sheet.")
    
    # Only allow graphing for columns that start with 'Q'
    q_columns = [c for c in headers if c.strip().upper().startswith('Q')]
    valid_graph_cols = [c for c in q_columns if c not in pii_to_strip]
    
    report_graph_cols = st.sidebar.multiselect(
        "Columns for Charts:",
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
            
            # --- SANITIZED CHART LOGIC ---
            # 1. Get counts
            chart_data = get_clean_value_counts(df[selected_vis]).reset_index()
            # 2. Force clean column names (remove leading spaces/special chars for the chart engine)
            chart_data.columns = ['Metric', 'Count']
            
            # 3. Display with a generic index to avoid encoding errors
            with col_left.expander(f"📊 {selected_vis} Chart", expanded=True):
                st.bar_chart(chart_data.set_index('Metric'), color="#2ECC71") # Modern Green
            
            # Qualification Rate (Same sanitization)
            if 'Status' in df.columns:
                with col_right.expander("📋 Qualification Overview", expanded=True):
                    status_data = get_clean_value_counts(df['Status']).reset_index()
                    status_data.columns = ['Status_Label', 'Count']
                    st.bar_chart(status_data.set_index('Status_Label'), color="#2ECC71") # Modern Green
            else:
                col_right.warning("No 'Status' column found. Run the Auditor first for full metrics.")

    with tab2:
        # Show what the client will actually see
        display_df = df.drop(columns=pii_to_strip, errors='ignore')
        st.dataframe(display_df.head(50))
        st.caption(f"Showing first 50 rows. Total rows: {len(df)}")

    # --- 5. EXCEL REPORT GENERATION ---
    st.divider()
    if st.button("📦 Generate & Download Client Excel Report"):
        try:
            output = io.BytesIO()
            # Use XlsxWriter as the engine for advanced formatting
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                
                # A. Raw Anonymized Data Sheet
                display_df.to_excel(writer, sheet_name='Anonymized Data', index=False)
                
                # B. Summary / Charts Sheet
                workbook = writer.book
                summary_sheet = workbook.add_worksheet('Summary')
                
                # --- FORMATTING: TURN OFF GRIDLINES ---
                summary_sheet.hide_gridlines(2)
                
                # Header Styles
                title_fmt = workbook.add_format({
                    'bold': True, 
                    'font_size': 16, 
                    'font_color': '#ffffff', 
                    'bg_color': '#4472C4', 
                    'align': 'center',
                    'valign': 'vcenter'
                })
                stat_header_fmt = workbook.add_format({'bold': True, 'bottom': 1, 'font_size': 12, 'text_wrap': True, 'valign': 'top'})
                italic_fmt = workbook.add_format({'italic': True})

                # Write Main Title
                file_title = uploaded_file.name.split('.')[0].upper()
                summary_sheet.merge_range('A1:H2', file_title, title_fmt)
                summary_sheet.write('A4', f'Total Respondents: {len(df)}', italic_fmt)

                pct_format_col = workbook.add_format({'num_format': '0.0%'})
                summary_sheet.set_column('A:A', 40)
                summary_sheet.set_column('B:B', 12)
                summary_sheet.set_column('C:C', 12, pct_format_col)

                # Use user-selected columns for summary charts
                demo_cols = report_graph_cols
                
                current_row = 6
                for col_name in demo_cols:
                    # 1. Write Data Table for the Charts
                    summary_sheet.write(current_row, 0, f"{col_name}", stat_header_fmt)
                    summary_sheet.write(current_row, 1, "Count", stat_header_fmt)
                    summary_sheet.write(current_row, 2, "Percent", stat_header_fmt)
                    
                    stats_series = get_clean_value_counts(df[col_name])
                    if len(stats_series) == 0:
                        continue
                        
                    stats_df = pd.DataFrame({
                        'Count': stats_series,
                        'Percentage': stats_series / stats_series.sum()
                    })
                    
                    # Write values mapping index->0, Count->1, Percentage->2
                    stats_df.to_excel(writer, sheet_name='Summary', startrow=current_row + 1, startcol=0, header=False)
                    
                    # Calculate dynamic dimensions for charts to prevent text overlap
                    # Horizontal bar charts need height dependent on number of categories
                    dynamic_height = max(350, len(stats_df) * 45)
                    # Width expanded to give long questions proper reading room
                    dynamic_width = max(550, len(stats_df) * 50)
                    
                    # --- CHART 1 (COUNT) ---
                    chart1 = workbook.add_chart({'type': 'bar'})
                    chart1.set_size({'width': dynamic_width, 'height': dynamic_height})
                    
                    chart1.add_series({
                        'name': f'{col_name} Counts',
                        'categories': ['Summary', current_row + 1, 0, current_row + len(stats_df), 0],
                        'values':     ['Summary', current_row + 1, 1, current_row + len(stats_df), 1],
                        'data_labels': {
                            'value': True, 
                            'font': {'color': '#404040', 'size': 10}
                        },
                        'fill': {'color': '#9E9E9E'}, # Millenial Grey
                        'border': {'none': True},     
                        'gap': 80                    
                    })
                    
                    chart1.set_title({
                        'name': f'Count of Total {col_name}',
                        'name_font': {'size': 12, 'color': '#595959', 'bold': False}
                    })
                    
                    chart1.set_legend({'none': True}) 
                    chart1.set_chartarea({'border': {'none': True}}) 
                    chart1.set_plotarea({'border': {'none': True}})
                    
                    # Axes are swapped for horizontal bar charts. 
                    # Y-axis is now categories, X-axis is values.
                    chart1.set_y_axis({
                        'line': {'color': '#D9D9D9'}, 
                        'num_font': {'color': '#595959'}, 
                        'reverse': True # Renders top-to-bottom
                    })
                    chart1.set_x_axis({
                        'line': {'none': True}, 
                        'major_gridlines': {'visible': True, 'line': {'color': '#F2F2F2'}}, 
                        'num_font': {'color': '#595959'}
                    })
                    
                    summary_sheet.insert_chart(current_row, 4, chart1)

                    # --- CHART 2 (PERCENTAGE) ---
                    chart2 = workbook.add_chart({'type': 'bar'})
                    chart2.set_size({'width': dynamic_width, 'height': dynamic_height})
                    
                    chart2.add_series({
                        'name': f'{col_name} Percentages',
                        'categories': ['Summary', current_row + 1, 0, current_row + len(stats_df), 0],
                        'values':     ['Summary', current_row + 1, 2, current_row + len(stats_df), 2],
                        'data_labels': {
                            'value': True, 
                            'font': {'color': '#404040', 'size': 10},
                            'num_format': '0%'
                        },
                        'fill': {'color': '#4472C4'}, # Corporate Blue for distinction
                        'border': {'none': True},     
                        'gap': 80                    
                    })
                    
                    chart2.set_title({
                        'name': f'% of Total {col_name}',
                        'name_font': {'size': 12, 'color': '#595959', 'bold': False}
                    })
                    
                    chart2.set_legend({'none': True}) 
                    chart2.set_chartarea({'border': {'none': True}}) 
                    chart2.set_plotarea({'border': {'none': True}})
                    
                    chart2.set_y_axis({
                        'line': {'color': '#D9D9D9'}, 
                        'num_font': {'color': '#595959'}, 
                        'reverse': True 
                    })
                    chart2.set_x_axis({
                        'line': {'none': True}, 
                        'major_gridlines': {'visible': True, 'line': {'color': '#F2F2F2'}}, 
                        'num_font': {'color': '#595959'},
                        'num_format': '0%'
                    })
                    
                    # Dynamically space Chart 2 to the right of Chart 1
                    col_offset = 4 + int(dynamic_width / 64) + 1
                    summary_sheet.insert_chart(current_row, col_offset, chart2)
                    
                    # Increment row for next section, accounting for dynamic graph height
                    current_row += max(len(stats_df) + 4, int(dynamic_height / 20) + 2)

                # Format the Data Sheet (Auto-filter and freeze panes)
                data_sheet = writer.sheets['Anonymized Data']
                data_sheet.autofilter(0, 0, 0, len(display_df.columns) - 1)
                data_sheet.freeze_panes(1, 0)
                # Optional: Set column widths for data sheet
                data_sheet.set_column(0, len(display_df.columns) - 1, 15)

            # Final Download Trigger
            processed_data = output.getvalue()
            st.download_button(
                label="📥 Click to Download .xlsx Report",
                data=processed_data,
                file_name=f"PFR_Client_Report_{uploaded_file.name.split('.')[0]}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            st.success("✅ Summary Report Ready!")
            
        except Exception as e:
            st.error(f"Error generating report: {e}")

else:
    st.info("👋 Welcome! Please upload an audited CSV or Excel file to generate the client report.")