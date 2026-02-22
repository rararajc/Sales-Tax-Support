import streamlit as st
import pandas as pd
from supabase import create_client, Client
from datetime import datetime

# --- DB CONNECTION ---
URL = st.secrets["SUPABASE_URL"]
KEY = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(URL, KEY)

st.set_page_config(page_title="Sales Tax Processor Pro", layout="wide")

# --- SIDEBAR SETTINGS ---
st.sidebar.title("⚙️ Settings")
tax_rate_input = st.sidebar.number_input("Sales Tax Rate (%)", value=10.25, step=0.01, format="%.2f")
tax_rate = tax_rate_input / 100

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
        tab1, tab2 = st.tabs(["📤 Upload & Process", "📊 Admin Records & Filing"])
    else:
        tab1 = st.container()
        tab2 = None

    # --- TAB 1: UPLOAD & PROCESS ---
    with tab1:
        st.header("📤 Process Sales Data")
        uploaded_file = st.file_uploader("Upload Excel or CSV", type=["xlsx", "csv"])

        if uploaded_file:
            if uploaded_file.name.endswith('.csv'):
                df = pd.read_csv(uploaded_file, sep=None, engine='python')
            else:
                df = pd.read_excel(uploaded_file)
            
            df.columns = [str(c).strip() for c in df.columns] 
            
            if 'Trans ID' in df.columns:
                df = df.drop_duplicates(subset=['Trans ID'])

            valid_statuses = ['funded', 'voided']
            main_df = df[(df['Status'].astype(str).str.lower().isin(valid_statuses)) & 
                         (df['Type'].astype(str).str.lower() == 'sale')].copy()

            if main_df.empty:
                st.warning("No records found matching 'funded/voided' and 'Sale'.")
            else:
                main_df['Date'] = pd.to_datetime(main_df['Date'])
                main_df['Month'] = main_df['Date'].dt.to_period('M').astype(str)
                main_df['Fee'] = main_df['Fee'] * -1
                main_df['Amount'] = main_df.apply(
                    lambda x: x['Amount'] * -1 if str(x['Status']).lower() == 'voided' else x['Amount'], axis=1
                )

                # --- TAXABLE IDENTIFICATION ---
                main_df['is_taxable'] = main_df['Amount'].abs().apply(lambda x: (x % 1 != 0) or (x > 2000))
                main_df['Category'] = main_df['is_taxable'].map({True: "Taxable", False: "Nontaxable"})
                
                # Calculations
                main_df['Taxable Sales Before Tax'] = main_df.apply(lambda x: x['Amount'] / (1 + tax_rate) if x['is_taxable'] else 0, axis=1)
                main_df['Nontaxable Sales'] = main_df.apply(lambda x: x['Amount'] if not x['is_taxable'] else 0, axis=1)
                main_df['Calculated Tax'] = main_df['Taxable Sales Before Tax'] * tax_rate

                # --- DISPLAY SECTION: DETAILED BREAKDOWN ---
                st.subheader("🔍 Itemized Tax Identification")
                st.write("This section shows how each transaction was classified.")
                st.dataframe(main_df[['Date', 'Trans ID', 'Amount', 'Category', 'Taxable Sales Before Tax', 'Nontaxable Sales', 'Calculated Tax']].style.format({
                    'Amount': "${:,.2f}", 'Taxable Sales Before Tax': "${:,.2f}", 'Nontaxable Sales': "${:,.2f}", 'Calculated Tax': "${:,.2f}"
                }))

                # --- DISPLAY SECTION: SUMMARY ---
                st.subheader("📋 Monthly Summary")
                summary_data = main_df.groupby('Month').apply(lambda x: pd.Series({
                    'Taxable Sales Before Tax': x['Taxable Sales Before Tax'].sum(),
                    'Nontaxable Sales': x['Nontaxable Sales'].sum(),
                    'Total Sales (Pre-Tax)': x['Taxable Sales Before Tax'].sum() + x['Nontaxable Sales'].sum(),
                    'Sales Tax Liability': x['Calculated Tax'].sum(),
                    'Grand Total (Collected)': x['Amount'].sum()
                })).reset_index().set_index('Month')
                st.dataframe(summary_data.style.format("${:,.2f}"))

                if st.button("🚀 Sync to Database (Avoids Duplicates)"):
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
                        # on_conflict="trans_id" ensures duplicates are updated, not added
                        supabase.table("logs").upsert(rows, on_conflict="trans_id").execute()
                        st.success("Database synchronized! Duplicate Trans IDs were updated.")
                    except Exception as e:
                        st.error(f"Database Error: {e}")

    # --- TAB 2: ADMIN RECORDS & FILING ---
    if tab2 is not None:
        with tab2:
            st.header("📊 Historical Records & Filing Status")
            try:
                # Fetching logs
                res = supabase.table("logs").select("*").order("date_field", desc=True).execute()
                if res.data:
                    admin_df = pd.DataFrame(res.data)
                    admin_df['date_field'] = pd.to_datetime(admin_df['date_field'])
                    admin_df['Month'] = admin_df['date_field'].dt.to_period('M').astype(str)
                    
                    # Basic calculations for historical view
                    admin_df['Taxable Sales'] = admin_df.apply(lambda x: x['amount'] / (1 + tax_rate) if x['is_taxable'] else 0, axis=1)
                    admin_df['Tax Liability'] = admin_df['Taxable Sales'] * tax_rate

                    # --- FILING INTERFACE ---
                    st.subheader("📅 Mark Month as Filed")
                    col1, col2, col3 = st.columns(3)
                    
                    available_months = sorted(admin_df['Month'].unique(), reverse=True)
                    target_month = col1.selectbox("Select Month", available_months)
                    filing_date = col2.date_input("Date Filed", datetime.now())
                    
                    if col3.button("Confirm Filing"):
                        # Update all records for that month in the database
                        # Note: This assumes your 'logs' table has 'is_filed' (bool) and 'date_filed' (date) columns
                        try:
                            # We filter by the date range of the selected month
                            start_date = f"{target_month}-01"
                            # Streamlit/Supabase update call
                            supabase.table("logs").update({
                                "is_filed": True, 
                                "date_filed": filing_date.strftime('%Y-%m-%d')
                            }).filter("date_field", "gte", start_date).execute()
                            st.success(f"Month {target_month} marked as filed on {filing_date}!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Filing Error: {e}. (Ensure 'is_filed' and 'date_filed' columns exist in Supabase)")

                    st.divider()

                    # --- AGGREGATED VIEW ---
                    st.subheader("📈 Financial Overview")
                    hist_summary = admin_df.groupby('Month').apply(lambda x: pd.Series({
                        'Total Amount': x['amount'].sum(),
                        'Tax Liability': x['Tax Liability'].sum(),
                        'Status': "✅ Filed" if x.get('is_filed', pd.Series([False])).any() else "❌ Unfiled",
                        'Date Filed': x.get('date_filed', pd.Series(["N/A"])).iloc[0]
                    }))
                    st.dataframe(hist_summary)

                    # --- CSV EXPORT ---
                    csv = admin_df.to_csv(index=False).encode('utf-8')
                    st.download_button("📥 Download Full Audit Log", data=csv, file_name='audit_log.csv', mime='text/csv')
                else:
                    st.info("No records found.")
            except Exception as e:
                st.error(f"Database Error: {e}")
