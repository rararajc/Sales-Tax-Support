import streamlit as st
import pandas as pd
from supabase import create_client, Client

# --- DB CONNECTION ---
URL = st.secrets["SUPABASE_URL"]
KEY = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(URL, KEY)

st.set_page_config(page_title="Sales Tax Processor", layout="wide")

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
        res = supabase.table("users").select("*").eq("username", u).eq("password", p).execute()
        if res.data:
            st.session_state.logged_in, st.session_state.username, st.session_state.role = True, u, res.data[0]['role']
            st.rerun()
        else:
            st.error("Invalid credentials")
else:
    st.sidebar.divider()
    st.sidebar.write(f"Logged in: **{st.session_state.username}**")
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
            
            # Filter: Include both 'funded' and 'voided' for Sale types
            valid_statuses = ['funded', 'voided']
            main_df = df[(df['Status'].astype(str).str.lower().isin(valid_statuses)) & 
                         (df['Type'].astype(str).str.lower() == 'sale')].copy()

            if main_df.empty:
                st.warning("No records found matching 'funded/voided' and 'Sale'.")
            else:
                main_df['Date'] = pd.to_datetime(main_df['Date'])
                main_df['Month'] = main_df['Date'].dt.to_period('M').astype(str)
                
                # REVERSED FEE LOGIC
                main_df['Fee'] = main_df['Fee'] * -1
                
                # VOID LOGIC: If voided, make amount negative to net out
                main_df['Amount'] = main_df.apply(lambda x: x['Amount'] * -1 if str(x['Status']).lower() == 'voided' else x['Amount'], axis=1)

                # TAXABLE LOGIC
                main_df['is_taxable'] = main_df['Amount'].abs().apply(lambda x: (x % 1 != 0) or (x > 4000))

                # --- 1. MONTHLY SUMMARY REPORT ---
                st.subheader("📋 Monthly Summary (Current File)")
                summary_data = main_df.groupby('Month').apply(lambda x: pd.Series({
                    'Total Nontaxable': x[x['is_taxable'] == False]['Amount'].sum(),
                    'Total Taxable': x[x['is_taxable'] == True]['Amount'].sum(),
                    'Sales Tax Due': x[x['is_taxable'] == True]['Amount'].sum() * tax_rate,
                    'Grand Total': x['Amount'].sum(),
                    'Total Fees': x['Fee'].sum()
                })).reset_index().set_index('Month')
                st.dataframe(summary_data.style.format("${:,.2f}"))

                if st.button("Save/Update Records to Database"):
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
                    supabase.table("logs").upsert(rows, on_conflict="trans_id").execute()
                    st.success("Database synchronized! (Voids netted and tax calculated)")

    if tab2:
        with tab2:
            st.header("📊 Admin Database Records")
            try:
                res = supabase.table("logs").select("*").order("date_field", desc=True).execute()
                if res.data:
                    admin_df = pd.DataFrame(res.data)
                    admin_df['date_field'] = pd.to_datetime(admin_df['date_field'])
                    admin_df['Month'] = admin_df['date_field'].dt.to_period('M').astype(str)

                    # --- HISTORICAL ACCUMULATED ---
                    st.subheader(f"📈 Accumulated Totals (at {tax_rate_input}%)")
                    
                    hist_summary = admin_df.groupby('Month').apply(lambda x: pd.Series({
                        'Taxable Sales': x[x['is_taxable'] == True]['amount'].sum(),
                        'Sales Tax': x[x['is_taxable'] == True]['amount'].sum() * tax_rate,
                        'Nontaxable Sales': x[x['is_taxable'] == False]['amount'].sum(),
                        'Total Fees': x['fee'].sum(),
                        'Grand Total': x['amount'].sum()
                    }))

                    # YTD Calculation
                    ytd = pd.DataFrame({
                        'Taxable Sales': [hist_summary['Taxable Sales'].sum()],
                        'Sales Tax': [hist_summary['Sales Tax'].sum()],
                        'Nontaxable Sales': [hist_summary['Nontaxable Sales'].sum()],
                        'Total Fees': [hist_summary['Total Fees'].sum()],
                        'Grand Total': [hist_summary['Grand Total'].sum()]
                    }, index=['TOTAL (YTD)'])

                    st.dataframe(pd.concat([hist_summary, ytd]).style.format("${:,.2f}"))

                    st.subheader("📝 Detailed Transaction Logs")
                    st.dataframe(admin_df[["trans_id", "date_field", "status", "amount", "fee", "is_taxable"]])
                else:
                    st.info("No records found.")
            except Exception as e:
                st.error(f"Database Error: {e}")
