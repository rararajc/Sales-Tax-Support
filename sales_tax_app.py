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
            try:
                # Load data
                if uploaded_file.name.endswith('.csv'):
                    df = pd.read_csv(uploaded_file, sep=None, engine='python')
                else:
                    df = pd.read_excel(uploaded_file)
                
                df.columns = [str(c).strip() for c in df.columns] 
                
                # --- DATA CLEANING (Handles Accounting Formats & Currency) ---
                for col in ['Amount', 'Fee']:
                    if col in df.columns:
                        # 1. Convert to string and clean whitespace
                        df[col] = df[col].astype(str).str.strip()
                        # 2. Handle parentheses (46.63) -> -46.63
                        df[col] = df[col].str.replace(r'\((.*)\)', r'-\1', regex=True)
                        # 3. Remove currency symbols, commas, and convert to float
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
                    st.warning("No records found matching 'funded/voided' and 'Sale'.")
                else:
                    main_df['Date'] = pd.to_datetime(main_df['Date'])
                    main_df['Month'] = main_df['Date'].dt.to_period('M').astype(str)
                    main_df['Fee'] = main_df['Fee'] * -1
                    
                    # Void Netting Logic
                    main_df['Amount'] = main_df.apply(
                        lambda x: x['Amount'] * -1 if str(x['Status']).lower() == 'voided' else x['Amount'], axis=1
                    )

                    # --- TAXABLE IDENTIFICATION ---
                    # Taxable = Has decimals OR is whole number > 2000
                    main_df['is_taxable'] = main_df['Amount'].abs().apply(lambda x: (x % 1 != 0) or (x > 2000))
                    main_df['Category'] = main_df['is_taxable'].map({True: "Taxable", False: "Nontaxable"})
                    
                    # Calculations
                    main_df['Taxable Sales Before Tax'] = main_df.apply(lambda x: x['Amount'] / (1 + tax_rate) if x['is_taxable'] else 0, axis=1)
                    main_df['Nontaxable Sales'] = main_df.apply(lambda x: x['Amount'] if not x['is_taxable'] else 0, axis=1)
                    main_df['Calculated Tax'] = main_df['Taxable Sales Before Tax'] * tax_rate

                    # --- DISPLAY: ITEMIZED BREAKDOWN ---
                    st.subheader("🔍 Itemized Tax Identification")
                    st.write("Review how each transaction was categorized before saving.")
                    st.dataframe(main_df[['Date', 'Trans ID', 'Amount', 'Category', 'Taxable Sales Before Tax', 'Nontaxable Sales', 'Calculated Tax']].style.format({
                        'Amount': "${:,.2f}", 'Taxable Sales Before Tax': "${:,.2f}", 'Nontaxable Sales': "${:,.2f}", 'Calculated Tax': "${:,.2f}"
                    }), use_container_width=True)

                    # --- DISPLAY: MONTHLY SUMMARY ---
                    st.subheader("📋 Monthly Summary")
                    summary_data = main_df.groupby('Month').apply(lambda x: pd.Series({
                        'Taxable Sales (Pre-Tax)': x['Taxable Sales Before Tax'].sum(),
                        'Nontaxable Sales': x['Nontaxable Sales'].sum(),
                        'Total Sales (Pre-Tax)': x['Taxable Sales Before Tax'].sum() + x['Nontaxable Sales'].sum(),
                        'Tax Liability': x['Calculated Tax'].sum(),
                        'Total Collected': x['Amount'].sum()
                    }), include_groups=False).reset_index().set_index('Month')
                    st.dataframe(summary_data.style.format("${:,.2f}"), use_container_width=True)

                    if st.button("🚀 Sync to Database (Upsert)"):
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
                            # Upsert prevents duplicates by updating existing trans_id matches
                            supabase.table("logs").upsert(rows, on_conflict="trans_id").execute()
                            st.success("Database synchronized successfully!")
                        except Exception as e:
                            st.error(f"Database Error: {e}")
            except Exception as e:
                st.error(f"File Error: {e}")

    # --- TAB 2: ADMIN RECORDS & FILING ---
    if tab2 is not None:
        with tab2:
            st.header("📊 Historical Database & Filing")
            try:
                res = supabase.table("logs").select("*").order("date_field", desc=True).execute()
                if res.data:
                    admin_df = pd.DataFrame(res.data)
                    admin_df['date_field'] = pd.to_datetime(admin_df['date_field'])
                    admin_df['Month'] = admin_df['date_field'].dt.to_period('M').astype(str)
                    
                    # Apply calculations based on DB flag
                    admin_df['Taxable Sales'] = admin_df.apply(lambda x: x['amount'] / (1 + tax_rate) if x['is_taxable'] else 0, axis=1)
                    admin_df['Tax Liability'] = admin_df['Taxable Sales'] * tax_rate

                    # --- FILING FORM ---
                    st.subheader("📅 Mark Month as Filed")
                    c1, c2, c3 = st.columns(3)
                    avail_months = sorted(admin_df['Month'].unique(), reverse=True)
                    target_month = c1.selectbox("Select Month", avail_months)
                    file_date = c2.date_input("Filing Date", datetime.now())
                    
                    if c3.button("Confirm Filing Status"):
                        try:
                            # Updates all rows within that month's date range
                            supabase.table("logs").update({
                                "is_filed": True, 
                                "date_filed": file_date.strftime('%Y-%m-%d')
                            }).filter("date_field", "gte", f"{target_month}-01")\
                              .filter("date_field", "lte", f"{target_month}-31").execute()
                            st.success(f"Records for {target_month} updated!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Update Error: {e} (Check if 'is_filed' column exists)")

                    st.divider()

                    # --- FINANCIAL SUMMARY ---
                    st.subheader("📈 Financial Overview")
                    hist_summary = admin_df.groupby('Month').apply(lambda x: pd.Series({
                        'Total Amount': x['amount'].sum(),
                        'Tax Liability': x['Tax Liability'].sum(),
                        'Filing Status': "✅ Filed" if x.get('is_filed', pd.Series([False])).any() else "❌ Unfiled",
                        'Date Filed': x.get('date_filed', pd.Series(["N/A"])).iloc[0]
                    }), include_groups=False)
                    st.dataframe(hist_summary.style.format({'Total Amount': "${:,.2f}", 'Tax Liability': "${:,.2f}"}), use_container_width=True)

                    # --- DOWNLOAD ---
                    csv = admin_df.to_csv(index=False).encode('utf-8')
                    st.download_button("📥 Download Audit Report", data=csv, file_name='tax_audit.csv', mime='text/csv')
                else:
                    st.info("No records found.")
            except Exception as e:
                st.error(f"Database Error: {e}")
