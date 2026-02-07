import streamlit as st
import pandas as pd
from supabase import create_client, Client

# --- SETUP ---
# Retrieve these from Streamlit Secrets or local .streamlit/secrets.toml
URL = st.secrets["SUPABASE_URL"]
KEY = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(URL, KEY)

st.set_page_config(page_title="Excel Sum Pro", layout="centered")

# --- SESSION STATE ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.role = ""

# --- LOGIN ---
def login():
    st.title("🔑 Employee Login")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")
    
    if st.button("Login"):
        res = supabase.table("users").select("*").eq("username", u).eq("password", p).execute()
        if res.data:
            st.session_state.logged_in = True
            st.session_state.username = u
            st.session_state.role = res.data[0]['role']
            st.rerun()
        else:
            st.error("Invalid credentials")

# --- ADMIN VIEW ---
def admin_view():
    st.header("📊 Admin Monthly Records")
    # Fetch all logs
    res = supabase.table("logs").select("*").execute()
    if res.data:
        df = pd.DataFrame(res.data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df['Month'] = df['timestamp'].dt.to_period('M').astype(str)
        
        # Monthly summary
        summary = df.groupby('Month').agg({
            'id': 'count',
            'total_sum': 'sum'
        }).rename(columns={'id': 'Files Processed', 'total_sum': 'Global Total'})
        
        st.table(summary)
        st.dataframe(df) # Detailed view
    else:
        st.info("No records found.")

# --- MAIN APP ---
if not st.session_state.logged_in:
    login()
else:
    st.sidebar.title(f"Welcome, {st.session_state.username}")
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.rerun()

    if st.session_state.role == "admin":
        tab1, tab2 = st.tabs(["Calculator", "Admin Dashboard"])
    else:
        tab1 = st.container()
        tab2 = None

    with tab1:
        st.title("📤 Excel Processor")
        uploaded_file = st.file_uploader("Choose an Excel file", type="xlsx")

        if uploaded_file:
            df_dict = pd.read_excel(uploaded_file, sheet_name=None)
            w_sum, d_sum = 0.0, 0.0

            for df in df_dict.values():
                nums = pd.to_numeric(df.values.flatten(), errors='coerce')
                nums = nums[~pd.isna(nums)]
                for n in nums:
                    if n % 1 == 0: w_sum += n
                    else: d_sum += n

            total = w_sum + d_sum
            
            st.success(f"Processing Complete!")
            col1, col2, col3 = st.columns(3)
            col1.metric("Whole Sum", f"{w_sum:,.2f}")
            col2.metric("Decimal Sum", f"{d_sum:,.2f}")
            col3.metric("Grand Total", f"{total:,.2f}")

            # LOG TO SUPABASE
            if st.button("Save Results to Records"):
                supabase.table("logs").insert({
                    "username": st.session_state.username,
                    "whole_sum": w_sum,
                    "decimal_sum": d_sum,
                    "total_sum": total
                }).execute()
                st.info("Record saved to database.")

    if tab2:
        with tab2:
            admin_view()