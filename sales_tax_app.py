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
                                "trans_id": tid,
