import streamlit as st
import pandas as pd
from supabase import create_client, Client
from io import BytesIO

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
        tab1, tab2 = st.tabs(["📤 Upload & Process", "📊 Admin Records"])
    else:
        tab1 = st.container()
        tab2 = None

    # --- TAB 1: UPLOAD & PROCESS ---
    with tab1:
        st.header("📤 Process Sales Data")
        uploaded_file = st.file_uploader("Upload Excel", type="xlsx")

        if uploaded_file:
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
                
                # Void Netting Logic
                main_df['Amount'] = main_df.apply(
                    lambda x: x['Amount'] * -1 if str(x['Status']).lower() == 'voided' else x['Amount'], axis=1
                )

                # --- UPDATED TAXABLE LOGIC ---
                # Nontaxable = Whole number AND <= 4000. 
                # Taxable = Has decimals OR is whole number > 4000.
                main_df['is_taxable'] = main_df['Amount'].abs().apply(
                    lambda x: (x % 1 != 0) or (x > 4000)
                )

                # Reverse Tax Calculation
                main_df['Taxable Sales Before Tax'] = main_df.apply(
                    lambda x: x['Amount'] / (1 + tax_rate) if x['is_taxable'] else 0, axis=1
                )
                main_df['Nontaxable Sales'] = main_df.apply(
                    lambda x: x['Amount'] if not x['is_taxable'] else 0, axis=1
                )
                main_df['Sales Tax (B)'] = main_df['Taxable Sales Before Tax'] * tax_rate

                st.subheader("📋 Monthly Summary (Current File)")
                
                summary_data = main_df.groupby('Month').apply(lambda x: pd.Series({
                    'Taxable Sales Before Tax': x['Taxable Sales Before Tax'].sum(),
                    'Nontaxable Sales': x['Nontaxable Sales'].sum(),
                    'Grand Total Sales Before Tax (A)': x['Taxable Sales Before Tax'].sum() + x['Nontaxable Sales'].sum(),
                    'Sales Tax (B)': x['Sales Tax (B)'].sum(),
                    'A+B': (x['Taxable Sales Before Tax'].sum() + x['Nontaxable Sales'].sum()) + x['Sales Tax (B)'].sum(),
                    'Sum of Amount per Excel': x['Amount'].sum(),
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
                    try:
                        supabase.table("logs").upsert(rows, on_conflict="trans_id").execute()
                        st.success("Database synchronized successfully!")
                    except Exception as e:
                        st.error(f"Database Error: {e}")

    # --- TAB 2: ADMIN RECORDS ---
    if tab2 is not None:
        with tab2:
            st.header("📊 Historical Database Records")
            try:
                res = supabase.table("logs").select("*").order("date_field", desc=True).execute()
                if res.data:
                    admin_df = pd.DataFrame(res.data)
                    admin_df['date_field'] = pd.to_datetime(admin_df['date_field'])
                    admin_df['Month'] = admin_df['date_field'].dt.to_period('M').astype(str)

                    # Re-calculate based on historical "is_taxable" flag saved in DB
                    admin_df['Taxable Sales Before Tax'] = admin_df.apply(
                        lambda x: x['amount'] / (1 + tax_rate) if x['is_taxable'] else 0, axis=1
                    )
                    admin_df['Nontaxable Sales'] = admin_df.apply(
                        lambda x: x['amount'] if not x['is_taxable'] else 0, axis=1
                    )
                    admin_df['Sales Tax (B)'] = admin_df['Taxable Sales Before Tax'] * tax_rate

                    st.subheader(f"📈 Accumulated Totals (at {tax_rate_input}%)")
                    
                    hist_summary = admin_df.groupby('Month').apply(lambda x: pd.Series({
                        'Taxable Sales Before Tax': x['Taxable Sales Before Tax'].sum(),
                        'Nontaxable Sales': x['Nontaxable Sales'].sum(),
                        'Grand Total Sales Before Tax (A)': x['Taxable Sales Before Tax'].sum() + x['Nontaxable Sales'].sum(),
                        'Sales Tax (B)': x['Sales Tax (B)'].sum(),
                        'A+B': (x['Taxable Sales Before Tax'].sum() + x['Nontaxable Sales'].sum()) + x['Sales Tax (B)'].sum(),
                        'Sum of Amount per Excel': x['amount'].sum(),
                        'Total Fees': x['fee'].sum()
                    }))

                    ytd_totals = hist_summary.sum().to_frame().T
                    ytd_totals.index = ['TOTAL (YTD)']

                    final_display = pd.concat([hist_summary, ytd_totals])
                    st.dataframe(final_display.style.format("${:,.2f}"))

                    st.subheader("📊 Sales Tax Liability Trend")
                    st.bar_chart(hist_summary[['Sales Tax (B)']])

                    csv = final_display.to_csv().encode('utf-8')
                    st.download_button(label="📥 Download Accumulated Report", data=csv, file_name='tax_report.csv', mime='text/csv')
                else:
                    st.info("No records found.")
            except Exception as e:
                st.error(f"Database Error: {e}")
