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
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.rerun()

    tab1, tab2 = st.tabs(["📤 Upload & Process", "📊 Admin Records & Monthly Filing"])

    # --- TAB 1: UPLOAD & PROCESS ---
    with tab1:
        st.header("📤 Process Sales Data")
        uploaded_file = st.file_uploader("Upload Excel or CSV", type=["xlsx", "csv"])

        if uploaded_file:
            try:
                df = pd.read_csv(uploaded_file, sep=None, engine='python') if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
                df.columns = [str(c).strip() for c in df.columns] 
                
                for col in ['Amount', 'Fee']:
                    if col in df.columns:
                        df[col] = df[col].astype(str).str.strip().str.replace(r'\((.*)\)', r'-\1', regex=True)
                        df[col] = (df[col].str.replace(r'[\$,\s]', '', regex=True).replace(['nan', 'None', '', '-'], '0').astype(float))

                main_df = df[(df['Status'].astype(str).str.lower().isin(['funded', 'voided'])) & (df['Type'].astype(str).str.lower() == 'sale')].copy()

                if not main_df.empty:
                    main_df['Date'] = pd.to_datetime(main_df['Date'])
                    main_df['Amount'] = main_df.apply(lambda x: x['Amount'] * -1 if str(x['Status']).lower() == 'voided' else x['Amount'], axis=1)
                    main_df['is_taxable'] = main_df['Amount'].abs().apply(lambda x: (x % 1 != 0))
                    main_df['Category'] = main_df['is_taxable'].map({True: "Taxable", False: "Nontaxable"})
                    
                    st.subheader("🔍 Itemized Upload Preview")
                    st.dataframe(main_df[['Date', 'Trans ID', 'Cardholder Name', 'Amount', 'Category']].style.format({'Amount': "${:,.2f}"}), use_container_width=True)

                    if st.button("🚀 Sync to Database"):
                        existing_res = supabase.table("logs").select("trans_id, is_taxable, is_filed, date_filed").execute()
                        db_registry = {item['trans_id']: item for item in existing_res.data}

                        rows = []
                        for _, r in main_df.iterrows():
                            tid = str(r.get("Trans ID"))
                            if tid in db_registry:
                                final_is_taxable = db_registry[tid]['is_taxable']
                                final_is_filed = db_registry[tid]['is_filed']
                                final_date_filed = db_registry[tid]['date_filed']
                            else:
                                final_is_taxable = bool(r["is_taxable"])
                                final_is_filed = False
                                final_date_filed = None

                            rows.append({
                                "trans_id": tid, "username": st.session_state.username, "date_field": r["Date"].strftime('%Y-%m-%d'),
                                "cardholder_name": str(r.get("Cardholder Name", "N/A")), "type": str(r["Type"]), "status": str(r["Status"]),
                                "amount": float(r["Amount"]), "fee": float(r.get("Fee", 0)), "is_taxable": final_is_taxable,
                                "is_filed": final_is_filed, "date_filed": final_date_filed
                            })
                        supabase.table("logs").upsert(rows, on_conflict="trans_id").execute()
                        st.success("Database updated!")
            except Exception as e:
                st.error(f"Error: {e}")

    # --- TAB 2: ADMIN RECORDS & FILING ---
    with tab2:
        try:
            res = supabase.table("logs").select("*").order("date_field", desc=True).execute()
            if res.data:
                all_df = pd.DataFrame(res.data)
                for col in ['is_filed', 'date_filed']:
                    if col not in all_df.columns: all_df[col] = None
                
                all_df['date_field'] = pd.to_datetime(all_df['date_field'])
                all_df['Month'] = all_df['date_field'].dt.to_period('M').astype(str)

                # Calculations
                all_df['Taxable Sales Before Tax'] = all_df.apply(lambda x: x['amount'] / (1 + tax_rate) if x['is_taxable'] else 0, axis=1)
                all_df['Nontaxable Sales'] = all_df.apply(lambda x: x['amount'] if not x['is_taxable'] else 0, axis=1)
                all_df['Total Tax (B)'] = all_df['Taxable Sales Before Tax'] * tax_rate
                
                # 1. SUMMARY
                st.header("📅 Monthly Sales Tax Summary")
                summary = all_df.groupby('Month').agg({'Taxable Sales Before Tax': 'sum', 'Nontaxable Sales': 'sum', 'Total Tax (B)': 'sum'})
                summary['Grand Total Sales (A)'] = summary['Taxable Sales Before Tax'] + summary['Nontaxable Sales']
                summary['A + B'] = summary['Grand Total Sales (A)'] + summary['Total Tax (B)']
                st.dataframe(summary.style.format("${:,.2f}"), use_container_width=True)

                # 2. UPDATED FILING TRACKER WITH ADJUSTABLE DATES
                st.divider()
                st.subheader("📝 Filing Tracker")
                filing_summary = all_df.groupby('Month').agg({'is_filed': 'max', 'date_filed': 'max'}).reset_index()

                for _, f_row in filing_summary.iterrows():
                    m = f_row['Month']
                    is_f = bool(f_row['is_filed'])
                    
                    # Determine initial date for the picker
                    current_d = datetime.strptime(f_row['date_filed'], '%Y-%m-%d').date() if f_row['date_filed'] else datetime.now().date()
                    
                    c1, c2, c3, c4, c5 = st.columns([1, 1, 1.5, 1, 1])
                    c1.write(f"**{m}**")
                    c2.write("✅ Filed" if is_f else "❌ Not Filed")
                    
                    # Date Picker for each month
                    new_date = c3.date_input("Filing Date", value=current_d, key=f"date_{m}", label_visibility="collapsed")
                    
                    if not is_f:
                        if c4.button(f"Mark Filed", key=f"f_{m}"):
                            supabase.table("logs").update({"is_filed": True, "date_filed": new_date.strftime('%Y-%m-%d')}).filter("date_field", "gte", f"{m}-01").filter("date_field", "lte", f"{m}-31").execute()
                            st.rerun()
                    else:
                        if c4.button(f"Update Date", key=f"up_{m}"):
                            supabase.table("logs").update({"date_filed": new_date.strftime('%Y-%m-%d')}).filter("date_field", "gte", f"{m}-01").filter("date_field", "lte", f"{m}-31").execute()
                            st.success(f"Updated {m}")
                            st.rerun()
                        if c5.button(f"Unmark", key=f"u_{m}"):
                            supabase.table("logs").update({"is_filed": False, "date_filed": None}).filter("date_field", "gte", f"{m}-01").filter("date_field", "lte", f"{m}-31").execute()
                            st.rerun()

                # 3. ITEMIZED & OVERRIDE
                st.divider()
                st.subheader("📋 Itemized Sales & Override")
                s_query = st.text_input("Search Cardholder / ID")
                audit_df = all_df.copy()
                if s_query:
                    audit_df = audit_df[(audit_df['cardholder_name'].str.contains(s_query, case=False)) | (audit_df['trans_id'].str.contains(s_query))]
                
                audit_df['Category'] = audit_df['is_taxable'].map({True: "Taxable", False: "Nontaxable"})
                st.dataframe(audit_df[['date_field', 'trans_id', 'cardholder_name', 'amount', 'Category', 'Total Tax (B)', 'is_filed', 'date_filed']].style.format({'amount': "${:,.2f}", 'Total Tax (B)': "${:,.2f}"}), use_container_width=True, hide_index=True)

                with st.expander("🛠️ Manual Tax Classification Override"):
                    target_id = st.selectbox("Select ID to Flip Status", audit_df['trans_id'].unique())
                    if st.button("Flip Taxable/Nontaxable Status"):
                        curr_is_taxable = audit_df[audit_df['trans_id'] == target_id]['is_taxable'].iloc[0]
                        supabase.table("logs").update({"is_taxable": not curr_is_taxable}).eq("trans_id", target_id).execute()
                        st.rerun()
            else:
                st.info("No records found.")
        except Exception as e:
            st.error(f"Error: {e}")
