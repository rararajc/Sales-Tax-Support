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
                    st.write("Review the classification below. You can toggle specific items if needed after syncing.")
                    st.dataframe(main_df[['Date', 'Trans ID', 'Cardholder Name', 'Amount', 'Category']].style.format({'Amount': "${:,.2f}"}), use_container_width=True)

                    if st.button("🚀 Sync to Database"):
                        rows = [{"trans_id": str(r.get("Trans ID")), "username": st.session_state.username, "date_field": r["Date"].strftime('%Y-%m-%d'), "cardholder_name": str(r.get("Cardholder Name", "N/A")), "type": str(r["Type"]), "status": str(r["Status"]), "amount": float(r["Amount"]), "fee": float(r.get("Fee", 0)), "is_taxable": bool(r["is_taxable"])} for _, r in main_df.iterrows()]
                        supabase.table("logs").upsert(rows, on_conflict="trans_id").execute()
                        st.success("Database updated! Visit the Admin tab to manage these records.")
            except Exception as e:
                st.error(f"Error: {e}")

    # --- TAB 2: ADMIN RECORDS & FILING ---
    with tab2:
        try:
            res = supabase.table("logs").select("*").order("date_field", desc=True).execute()
            if res.data:
                all_df = pd.DataFrame(res.data)
                all_df['date_field'] = pd.to_datetime(all_df['date_field'])
                all_df['Month'] = all_df['date_field'].dt.to_period('M').astype(str)

                # Core Calculations
                all_df['Taxable Sales Before Tax'] = all_df.apply(lambda x: x['amount'] / (1 + tax_rate) if x['is_taxable'] else 0, axis=1)
                all_df['Nontaxable Sales'] = all_df.apply(lambda x: x['amount'] if not x['is_taxable'] else 0, axis=1)
                all_df['Total Tax (B)'] = all_df['Taxable Sales Before Tax'] * tax_rate
                
                # 1. MONTHLY SALES TAX SUMMARY
                st.header("📅 Monthly Sales Tax Summary")
                summary = all_df.groupby('Month').agg({
                    'Taxable Sales Before Tax': 'sum',
                    'Nontaxable Sales': 'sum',
                    'Total Tax (B)': 'sum'
                })
                summary['Grand Total Sales (A)'] = summary['Taxable Sales Before Tax'] + summary['Nontaxable Sales']
                summary['A + B'] = summary['Grand Total Sales (A)'] + summary['Total Tax (B)']
                summary['Effective Rate'] = (summary['Total Tax (B)'] / summary['Grand Total Sales (A)'] * 100).fillna(0)

                st.dataframe(summary.style.format({
                    'Taxable Sales Before Tax': "${:,.2f}", 'Nontaxable Sales': "${:,.2f}",
                    'Grand Total Sales (A)': "${:,.2f}", 'Total Tax (B)': "${:,.2f}",
                    'A + B': "${:,.2f}", 'Effective Rate': "{:.2f}%"
                }), use_container_width=True)

                # 2. FILING TRACKER (LISTING ALL MONTHS)
                st.divider()
                st.subheader("📝 Filing Tracker")
                
                # Get filing info per month
                filing_info = all_df.groupby('Month').agg({
                    'is_filed': 'max', # True if any record in month is filed
                    'date_filed': 'max'
                }).reset_index()

                for _, f_row in filing_info.iterrows():
                    m = f_row['Month']
                    is_f = f_row['is_filed']
                    d_f = f_row['date_filed']
                    
                    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
                    c1.write(f"**{m}**")
                    c2.write("✅ Filed" if is_f else "❌ Not Filed")
                    c3.write(f"Date: {d_f}" if d_f else "---")
                    
                    if not is_f:
                        if c4.button(f"Mark {m} Filed", key=f"btn_{m}"):
                            # Default to today's date for quick filing
                            today_str = datetime.now().strftime('%Y-%m-%d')
                            supabase.table("logs").update({"is_filed": True, "date_filed": today_str}).filter("date_field", "gte", f"{m}-01").filter("date_field", "lte", f"{m}-31").execute()
                            st.rerun()
                    else:
                        if c4.button(f"Unmark {m}", key=f"un_{m}"):
                            supabase.table("logs").update({"is_filed": False, "date_filed": None}).filter("date_field", "gte", f"{m}-01").filter("date_field", "lte", f"{m}-31").execute()
                            st.rerun()

                # 3. ITEMIZED SALES INFORMATION & OVERRIDE
                st.divider()
                st.subheader("📋 Itemized Sales Information")
                
                s_query = st.text_input("Search by Cardholder Name or Trans ID")
                audit_df = all_df.copy()
                if s_query:
                    audit_df = audit_df[(audit_df['cardholder_name'].str.contains(s_query, case=False)) | (audit_df['trans_id'].str.contains(s_query))]
                
                audit_df['Category'] = audit_df['is_taxable'].map({True: "Taxable", False: "Nontaxable"})
                
                # Displaying Table
                st.dataframe(audit_df[['date_field', 'trans_id', 'cardholder_name', 'amount', 'Category', 'Total Tax (B)', 'is_filed']].style.format({'amount': "${:,.2f}", 'Total Tax (B)': "${:,.2f}"}), use_container_width=True, hide_index=True)

                # 4. ABILITY TO CHANGE TAXABLE TO NONTAXABLE
                with st.expander("🛠️ Manual Tax Classification Override"):
                    target_id = st.selectbox("Search/Select Trans ID to Flip Status", audit_df['trans_id'].unique())
                    current_row = audit_df[audit_df['trans_id'] == target_id].iloc[0]
                    st.info(f"Currently: **{current_row['Category']}** | Cardholder: {current_row['cardholder_name']} | Amount: ${current_row['amount']:,.2f}")
                    
                    if st.button("Flip Taxable/Nontaxable Status"):
                        new_state = not current_row['is_taxable']
                        supabase.table("logs").update({"is_taxable": new_state}).eq("trans_id", target_id).execute()
                        st.success(f"Transaction {target_id} updated!")
                        st.rerun()

            else:
                st.info("No records found in database.")
        except Exception as e:
            st.error(f"Error in Admin Tab: {e}")
