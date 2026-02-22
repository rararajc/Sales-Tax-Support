import streamlit as st
import pandas as pd
from supabase import create_client, Client
from datetime import datetime, date

# --- DB CONNECTION ---
URL = st.secrets["SUPABASE_URL"]
KEY = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(URL, KEY)

st.set_page_config(page_title="Sales Tax Processor Pro", layout="wide")

# --- SIDEBAR SETTINGS ---
st.sidebar.title("⚙️ Global Filters")

# Global Tax Rate
tax_rate_input = st.sidebar.number_input("Sales Tax Rate (%)", value=10.25, step=0.01, format="%.2f")
tax_rate = tax_rate_input / 100

st.sidebar.divider()

# Global Date Range
st.sidebar.subheader("📅 Date Range Filter")
today = date.today()
start_of_year = date(today.year, 1, 1)
date_range = st.sidebar.date_input(
    "Select Period for Analytics",
    value=(start_of_year, today),
    max_value=today
)

# --- LOGIN LOGIC ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("🔑 User Login")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")
    if st.button("Login"):
        try:
            res = supabase.table("users").select("*").eq("username", u).eq("password", p).execute()
            if res.data:
                st.session_state.logged_in = True
                st.session_state.username = u
                st.session_state.role = res.data[0]['role']
                st.rerun()
            else:
                st.error("Invalid credentials")
        except Exception as e:
            st.error(f"Login Error: {e}")
else:
    st.sidebar.divider()
    st.sidebar.write(f"Logged in: **{st.session_state.username}**")
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.rerun()

    if st.session_state.role == "admin":
        tab1, tab2 = st.tabs(["📤 Upload & Process", "📊 Admin Records & Analytics"])
    else:
        tab1 = st.container()
        tab2 = None

    # --- TAB 1: UPLOAD & PROCESS ---
    with tab1:
        st.header("📤 Process Sales Data")
        uploaded_file = st.file_uploader("Upload Excel or CSV", type=["xlsx", "csv"])

        if uploaded_file:
            try:
                if uploaded_file.name.endswith('.csv'):
                    df = pd.read_csv(uploaded_file, sep=None, engine='python')
                else:
                    df = pd.read_excel(uploaded_file)
                
                df.columns = [str(c).strip() for c in df.columns] 
                
                for col in ['Amount', 'Fee']:
                    if col in df.columns:
                        df[col] = df[col].astype(str).str.strip()
                        df[col] = df[col].str.replace(r'\((.*)\)', r'-\1', regex=True)
                        df[col] = (
                            df[col]
                            .str.replace(r'[\$,\s]', '', regex=True)
                            .replace(['nan', 'None', '', '-'], '0')
                            .astype(float)
                        )

                if 'Trans ID' in df.columns:
                    df = df.drop_duplicates(subset=['Trans ID'])

                valid_statuses = ['funded', 'voided']
                main_df = df[(df['Status'].astype(str).str.lower().isin(valid_statuses)) & 
                             (df['Type'].astype(str).str.lower() == 'sale')].copy()

                if main_df.empty:
                    st.warning("No valid sale records found.")
                else:
                    main_df['Date'] = pd.to_datetime(main_df['Date'])
                    main_df['Month'] = main_df['Date'].dt.to_period('M').astype(str)
                    main_df['Fee'] = main_df['Fee'] * -1
                    main_df['Amount'] = main_df.apply(
                        lambda x: x['Amount'] * -1 if str(x['Status']).lower() == 'voided' else x['Amount'], axis=1
                    )

                    main_df['is_taxable'] = main_df['Amount'].abs().apply(lambda x: (x % 1 != 0))
                    main_df['Category'] = main_df['is_taxable'].map({True: "Taxable", False: "Nontaxable"})
                    main_df['Taxable Sales Pre-Tax'] = main_df.apply(lambda x: x['Amount'] / (1 + tax_rate) if x['is_taxable'] else 0, axis=1)
                    main_df['Calculated Tax'] = main_df['Taxable Sales Pre-Tax'] * tax_rate

                    st.subheader("🔍 Upload Preview")
                    st.dataframe(main_df[['Date', 'Trans ID', 'Cardholder Name', 'Amount', 'Category', 'Calculated Tax']].style.format({
                        'Amount': "${:,.2f}", 'Calculated Tax': "${:,.2f}"
                    }), use_container_width=True)

                    if st.button("🚀 Sync to Database"):
                        rows = []
                        for _, row in main_df.iterrows():
                            rows.append({
                                "trans_id": str(row.get("Trans ID", "")),
                                "username": st.session_state.username,
                                "date_field": row["Date"].strftime('%Y-%m-%d'),
                                "cardholder_name": str(row.get("Cardholder Name", "N/A")),
                                "type": str(row["Type"]),
                                "status": str(row["Status"]),
                                "amount": float(row["Amount"]),
                                "fee": float(row["Fee"]),
                                "is_taxable": bool(row["is_taxable"])
                            })
                        try:
                            supabase.table("logs").upsert(rows, on_conflict="trans_id").execute()
                            st.success("Database updated!")
                        except Exception as e:
                            st.error(f"Database Error: {e}")
            except Exception as e:
                st.error(f"File Error: {e}")

    # --- TAB 2: ADMIN RECORDS & ANALYTICS ---
    if tab2 is not None:
        with tab2:
            try:
                # Fetch all for processing, but filter locally based on sidebar
                res = supabase.table("logs").select("*").order("date_field", desc=True).execute()
                if res.data:
                    full_df = pd.DataFrame(res.data)
                    full_df['date_field'] = pd.to_datetime(full_df['date_field'])
                    
                    # Apply Date Range Filter
                    if len(date_range) == 2:
                        sd, ed = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
                        filtered_period_df = full_df[(full_df['date_field'] >= sd) & (full_df['date_field'] <= ed)].copy()
                    else:
                        filtered_period_df = full_df.copy()

                    filtered_period_df['Month'] = filtered_period_df['date_field'].dt.to_period('M').astype(str)
                    
                    # Calculations
                    filtered_period_df['Taxable Vol'] = filtered_period_df.apply(lambda x: x['amount'] if x['is_taxable'] else 0, axis=1)
                    filtered_period_df['Nontaxable Vol'] = filtered_period_df.apply(lambda x: x['amount'] if not x['is_taxable'] else 0, axis=1)
                    filtered_period_df['Tax Liability'] = (filtered_period_df['Taxable Vol'] / (1 + tax_rate)) * tax_rate

                    # --- KPI CARDS ---
                    st.header(f"📊 Period Overview ({date_range[0]} to {date_range[1]})")
                    kpi1, kpi2, kpi3 = st.columns(3)
                    total_vol = filtered_period_df['amount'].sum()
                    total_tax = filtered_period_df['Tax Liability'].sum()
                    effective_rate = (total_tax / total_vol * 100) if total_vol != 0 else 0
                    
                    kpi1.metric("Total Sales Volume", f"${total_vol:,.2f}")
                    kpi2.metric("Total Tax Liability", f"${total_tax:,.2f}")
                    kpi3.metric("Effective Tax Rate", f"{effective_rate:.2f}%")

                    # --- ANALYTICS ---
                    st.divider()
                    col_chart1, col_chart2 = st.columns(2)
                    
                    with col_chart1:
                        st.subheader("📉 Volume Breakdown")
                        chart_data = filtered_period_df.groupby('Month')[['Taxable Vol', 'Nontaxable Vol']].sum()
                        st.bar_chart(chart_data)

                    with col_chart2:
                        st.subheader("📈 Tax Rate Trend (%)")
                        trend_data = filtered_period_df.groupby('Month').apply(lambda x: (x['Tax Liability'].sum() / x['amount'].sum() * 100) if x['amount'].sum() != 0 else 0)
                        st.line_chart(trend_data)

                    # --- SEARCH & OVERRIDE ---
                    st.divider()
                    st.subheader("🔎 Database Audit & Manual Override")
                    c_s1, c_s2 = st.columns(2)
                    s_name = c_s1.text_input("Search Cardholder", "")
                    s_id = c_s2.text_input("Search Trans ID", "")

                    f_search_df = filtered_period_df.copy()
                    if s_name:
                        f_search_df = f_search_df[f_search_df['cardholder_name'].str.contains(s_name, case=False, na=False)]
                    if s_id:
                        f_search_df = f_search_df[f_search_df['trans_id'].str.contains(s_id, case=False, na=False)]

                    with st.expander("🛠️ Edit Record Tax Status"):
                        if not f_search_df.empty:
                            target_id = st.selectbox("Select ID to Edit", f_search_df['trans_id'].unique())
                            row_info = f_search_df[f_search_df['trans_id'] == target_id].iloc[0]
                            st.info(f"ID: {target_id} | Name: {row_info['cardholder_name']} | Current: {'Taxable' if row_info['is_taxable'] else 'Nontaxable'}")
                            if st.button("Toggle Status"):
                                supabase.table("logs").update({"is_taxable": not row_info['is_taxable']}).eq("trans_id", target_id).execute()
                                st.rerun()
                        else:
                            st.write("No matching records in the current period.")

                    # --- FILING ---
                    st.divider()
                    st.subheader("📅 Filing Summary")
                    hist_summary = filtered_period_df.groupby('Month').apply(lambda x: pd.Series({
                        'Total Sales': x['amount'].sum(),
                        'Tax Liability': x['Tax Liability'].sum(),
                        'Status': "✅ Filed" if x.get('is_filed', pd.Series([False])).any() else "❌ Pending"
                    }), include_groups=False)
                    st.dataframe(hist_summary.style.format({'Total Sales': "${:,.2f}", 'Tax Liability': "${:,.2f}"}), use_container_width=True)

                    csv = filtered_period_df.to_csv(index=False).encode('utf-8')
                    st.download_button("📥 Export Period Audit Log", data=csv, file_name=f'audit_{date_range[0]}_{date_range[1]}.csv', mime='text/csv')
                else:
                    st.info("No records found.")
            except Exception as e:
                st.error(f"Database Error: {e}")
