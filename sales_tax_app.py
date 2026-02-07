import streamlit as st
import pandas as pd
from supabase import create_client, Client

# --- DB CONNECTION ---
URL = st.secrets["SUPABASE_URL"]
KEY = st.secrets["SUPABASE_KEY"]
supabase: Client = create_client(URL, KEY)

st.set_page_config(page_title="Sales Tax Processor", layout="wide")

# --- LOGIN (Simplified for brevity) ---
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
            df.columns = [str(c).strip() for c in df.columns] # Clean column names
            
            # 1. Basic Filter: Must be "funded" and "Sale"
            base_filter = (df['Status'].astype(str).str.lower() == 'funded') & \
                          (df['Type'].astype(str).str.lower() == 'sale')
            main_df = df[base_filter].copy()

            if main_df.empty:
                st.warning("No records found matching 'funded' and 'Sale'.")
            else:
                # 2. Add Helper Columns
                main_df['Date'] = pd.to_datetime(main_df['Date'])
                main_df['Month'] = main_df['Date'].dt.to_period('M').astype(str)
                main_df['is_decimal'] = main_df['Amount'].apply(lambda x: x % 1 != 0)

                # 3. Apply the specific logic
                # Bucket 1: Decimals (Any amount)
                decimal_mask = (main_df['is_decimal'] == True)
                
                # Bucket 2: Whole numbers (Only if <= 4000)
                whole_mask = (main_df['is_decimal'] == False) & (main_df['Amount'] <= 4000)

                # Combine valid rows for the final report
                valid_rows = main_df[decimal_mask | whole_mask].copy()

                # 4. Generate Monthly Summary
                st.subheader("Monthly Summary Report")
                monthly_summary = valid_rows.groupby('Month').agg(
                    Total_Decimals=('Amount', lambda x: x[main_df.loc[x.index, 'is_decimal']].sum()),
                    Total_Whole_Under_4k=('Amount', lambda x: x[~main_df.loc[x.index, 'is_decimal']].sum()),
                    Count=('Amount', 'count'),
                    Grand_Total=('Amount', 'sum')
                )
                
                st.dataframe(monthly_summary.style.format("{:,.2f}"))

                # Totals for current file
                st.divider()
                st.write(f"### File Totals")
                st.write(f"✅ **Decimal Sum (All):** ${valid_rows[valid_rows['is_decimal']]['Amount'].sum():,.2f}")
                st.write(f"✅ **Whole Sum (≤ 4000):** ${valid_rows[~valid_rows['is_decimal']]['Amount'].sum():,.2f}")
                st.write(f"🎯 **Grand Total:** ${valid_rows['Amount'].sum():,.2f}")

                if st.button("Save Filtered Data to Database"):
                    rows = []
                    for _, row in valid_rows.iterrows():
                        rows.append({
                            "username": st.session_state.username,
                            "trans_id": str(row["Trans ID"]),
                            "date_field": row["Date"].strftime('%Y-%m-%d'),
                            "cardholder_name": str(row["Cardholder Name"]),
                            "type": str(row["Type"]),
                            "status": str(row["Status"]),
                            "amount": float(row["Amount"]),
                            "fee": float(row["Fee"]),
                            "is_decimal": bool(row["is_decimal"])
                        })
                    supabase.table("logs").insert(rows).execute()
                    st.success(f"Logged {len(rows)} transactions.")

    if tab2:
        with tab2:
            st.header("📊 Admin Transaction Log")
            res = supabase.table("logs").select("*").order("date_field", desc=True).execute()
            if res.data:
                admin_df = pd.DataFrame(res.data)
                admin_df['date_field'] = pd.to_datetime(admin_df['date_field'])
                admin_df['Month'] = admin_df['date_field'].dt.to_period('M').astype(str)
                
                # Monthly Global View for Admin
                admin_summary = admin_df.groupby('Month')['amount'].sum()
                st.bar_chart(admin_summary)
                
                st.dataframe(admin_df[["trans_id", "date_field", "cardholder_name", "type", "status", "amount", "fee"]])
