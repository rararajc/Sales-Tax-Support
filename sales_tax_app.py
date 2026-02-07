import streamlit as st
import pandas as pd
from supabase import create_client, Client

# --- DB CONNECTION ---
URL = st.secrets["SUPABASE_URL"]
KEY = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(URL, KEY)

st.set_page_config(page_title="Sales Tax Processor", layout="wide")

# --- LOGIN LOGIC ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    st.title("🔑 User Login")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")
    if st.button("Login"):
        res = supabase.table("users").select("*").eq("username", u).eq("password", p).execute()
        if res.data:
            st.session_state.logged_in, st.session_state.username, st.session_state.role = True, u, res.data[0]['role']
            st.rerun()
        else:
            st.error("Invalid credentials")
else:
    st.sidebar.title(f"User: {st.session_state.username}")
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.rerun()

    tab1, tab2 = st.tabs(["Upload & Process", "Admin Records"]) if st.session_state.role == "admin" else ([st.container()], None)

    with tab1:
        st.header("📤 Process Sales Data")
        uploaded_file = st.file_uploader("Upload Excel", type="xlsx")

        if uploaded_file:
            df = pd.read_excel(uploaded_file)
            df.columns = [str(c).strip() for c in df.columns] 
            
            # Filter: Funded Sales only
            base_filter = (df['Status'].astype(str).str.lower() == 'funded') & \
                          (df['Type'].astype(str).str.lower() == 'sale')
            main_df = df[base_filter].copy()

            if main_df.empty:
                st.warning("No records found matching 'funded' and 'Sale'.")
            else:
                main_df['Date'] = pd.to_datetime(main_df['Date'])
                main_df['Month'] = main_df['Date'].dt.to_period('M').astype(str)
                
                # TAXABLE LOGIC: Decimal OR Whole Number > 4000
                main_df['is_taxable'] = main_df['Amount'].apply(lambda x: (x % 1 != 0) or (x > 4000))

                # --- 1. MONTHLY SUMMARY REPORT ---
                st.subheader("📋 Monthly Summary Report")
                summary_data = main_df.groupby('Month').apply(lambda x: pd.Series({
                    'Total Nontaxable': x[x['is_taxable'] == False]['Amount'].sum(),
                    'Total Taxable': x[x['is_taxable'] == True]['Amount'].sum(),
                    'Grand Total': x['Amount'].sum()
                })).reset_index().set_index('Month')
                st.dataframe(summary_data.style.format("${:,.2f}"))

                # --- 2. MONTHLY FEES SUMMARY ---
                st.subheader("💳 Monthly Fees Summary")
                fees_summary = main_df.groupby('Month')['Fee'].sum().reset_index().set_index('Month')
                st.dataframe(fees_summary.style.format("${:,.2f}"))

                if st.button("Save Records to Database"):
                    rows = []
                    for _, row in main_df.iterrows():
                        rows.append({
                            "username": st.session_state.username,
                            "trans_id": str(row.get("Trans ID", "")),
                            "date_field": row["Date"].strftime('%Y-%m-%d'),
                            "cardholder_name": str(row.get("Cardholder Name", "N/A")),
                            "type": str(row["Type"]),
                            "status": str(row["Status"]),
                            "amount": float(row["Amount"]),
                            "fee": float(row.get("Fee", 0)),
                            "is_taxable": bool(row["is_taxable"])
                        })
                    supabase.table("logs").insert(rows).execute()
                    st.success("All data logged successfully!")

    if tab2:
        with tab2:
            st.header("📊 Admin Database Records")
            try:
                res = supabase.table("logs").select("*").order("date_field", desc=True).execute()
                if res.data:
                    admin_df = pd.DataFrame(res.data)
                    admin_df['date_field'] = pd.to_datetime(admin_df['date_field'])
                    admin_df['Month'] = admin_df['date_field'].dt.to_period('M').astype(str)

                    # --- ACCUMULATED DATA BY MONTH ---
                    st.subheader("📈 Historical Accumulated Monthly Totals")
                    historical_summary = admin_df.groupby('Month').agg({
                        'amount': 'sum',
                        'fee': 'sum',
                        'id': 'count'
                    }).rename(columns={'amount': 'Total Sales', 'fee': 'Total Fees', 'id': 'Transaction Count'})
                    st.dataframe(historical_summary.style.format("${:,.2f}", subset=['Total Sales', 'Total Fees']))

                    # --- DETAILED LOGS ---
                    st.subheader("📝 Detailed Transaction Logs")
                    st.dataframe(admin_df[["trans_id", "date_field", "cardholder_name", "amount", "fee", "is_taxable"]])
                else:
                    st.info("No records found in database.")
            except Exception as e:
                st.error(f"Database Error: {e}")
