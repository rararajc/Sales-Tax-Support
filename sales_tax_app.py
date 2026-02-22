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
tax_rate_input = st.sidebar.number_input("Sales Tax Rate (%)", value=10.25, step=0.01, format="%.2f")
tax_rate = tax_rate_input / 100

st.sidebar.divider()

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
                        df[col] = (df[col].str.replace(r'[\$,\s]', '', regex=True)
                                   .replace(['nan', 'None', '', '-'], '0').astype(float))

                if 'Trans ID' in df.columns:
                    df = df.drop_duplicates(subset=['Trans ID'])

                valid_statuses = ['funded', 'voided']
                main_df = df[(df['Status'].astype(str).str.lower().isin(valid_statuses)) & 
                             (df['Type'].astype(str).str.lower() == 'sale')].copy()

                if main_df.empty:
                    st.warning("No valid sale records found.")
                else:
                    main_df['Date'] = pd.to_datetime(main_df['Date'])
                    main_df['Fee'] = main_df['Fee'] * -1
                    main_df['Amount'] = main_df.apply(
                        lambda x: x['Amount'] * -1 if str(x['Status']).lower() == 'voided' else x['Amount'], axis=1
                    )

                    main_df['is_taxable'] = main_df['Amount'].abs().apply(lambda x: (x % 1 != 0))
                    
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
                res = supabase.table("logs").select("*").order("date_field", desc=True).execute()
                if res.data:
                    full_df = pd.DataFrame(res.data)
                    full_df['date_field'] = pd.to_datetime(full_df['date_field'])
                    
                    # Apply Sidebar Date Filter
                    if len(date_range) == 2:
                        sd, ed = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
                        filtered_df = full_df[(full_df['date_field'] >= sd) & (full_df['date_field'] <= ed)].copy()
                    else:
                        filtered_df = full_df.copy()

                    # Dynamic Calculations
                    filtered_df['Category'] = filtered_df['is_taxable'].map({True: "Taxable", False: "Nontaxable"})
                    filtered_df['Taxable Vol'] = filtered_df.apply(lambda x: x['amount'] if x['is_taxable'] else 0, axis=1)
                    filtered_df['Calculated Tax'] = (filtered_df['Taxable Vol'] / (1 + tax_rate)) * tax_rate
                    filtered_df['Month'] = filtered_df['date_field'].dt.to_period('M').astype(str)

                    # --- TOP ANALYTICS ---
                    st.header(f"📊 Analytics Summary")
                    k1, k2, k3 = st.columns(3)
                    total_vol = filtered_df['amount'].sum()
                    total_tax = filtered_df['Calculated Tax'].sum()
                    k1.metric("Total Sales", f"${total_vol:,.2f}")
                    k2.metric("Total Tax", f"${total_tax:,.2f}")
                    k3.metric("Effective Rate", f"{(total_tax/total_vol*100 if total_vol !=0 else 0):.2f}%")

                    st.divider()

                    # --- SEARCH & TRANSACTION TABLE ---
                    st.subheader("📋 Itemized Transaction Log")
                    c_s1, c_s2 = st.columns(2)
                    s_name = c_s1.text_input("Filter by Name", "")
                    s_id = c_s2.text_input("Filter by Trans ID", "")

                    display_df = filtered_df.copy()
                    if s_name:
                        display_df = display_df[display_df['cardholder_name'].str.contains(s_name, case=False, na=False)]
                    if s_id:
                        display_df = display_df[display_df['trans_id'].str.contains(s_id, case=False, na=False)]

                    # THE MAIN REQUESTED TABLE
                    st.dataframe(display_df[['date_field', 'trans_id', 'cardholder_name', 'amount', 'Category', 'Calculated Tax']].style.format({
                        'amount': "${:,.2f}", 'Calculated Tax': "${:,.2f}"
                    }), use_container_width=True, hide_index=True)

                    # --- MANUAL OVERRIDE TOOL ---
                    with st.expander("🛠️ Quick Toggle Tax Status"):
                        if not display_df.empty:
                            target_id = st.selectbox("Select Trans ID to Flip", display_df['trans_id'].unique())
                            row_info = display_df[display_df['trans_id'] == target_id].iloc[0]
                            st.warning(f"Target: {row_info['cardholder_name']} | Current: {row_info['Category']}")
                            if st.button("Change Taxable ↔ Nontaxable"):
                                supabase.table("logs").update({"is_taxable": not row_info['is_taxable']}).eq("trans_id", target_id).execute()
                                st.success("Status updated. Recalculating totals...")
                                st.rerun()
                        else:
                            st.write("No records found in current search.")

                    # --- CHARTS ---
                    st.divider()
                    st.subheader("📈 Trends")
                    ch1, ch2 = st.columns(2)
                    with ch1:
                        st.bar_chart(filtered_df.groupby('Month')[['Taxable Vol', 'amount']].sum())
                    with ch2:
                        trend = filtered_df.groupby('Month').apply(lambda x: (x['Calculated Tax'].sum()/x['amount'].sum()*100) if x['amount'].sum()!=0 else 0)
                        st.line_chart(trend)

                    # --- EXPORT ---
                    csv = filtered_df.to_csv(index=False).encode('utf-8')
                    st.download_button("📥 Export Audit Report", data=csv, file_name='audit_log.csv', mime='text/csv')

                else:
                    st.info("No records found.")
            except Exception as e:
                st.error(f"Admin Tab Error: {e}")
