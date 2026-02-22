# ... (Keep your imports and login logic as is)

                # 2. UPDATED FILING TRACKER WITH ADJUSTABLE DATES
                st.divider()
                st.subheader("📝 Filing Tracker")
                
                # We group to see the status of each month
                filing_summary = all_df.groupby('Month').agg({'is_filed': 'max', 'date_filed': 'max'}).reset_index()

                for _, f_row in filing_summary.iterrows():
                    m = f_row['Month']
                    is_f = bool(f_row['is_filed'])
                    
                    # --- ROBUST DATE HANDLING ---
                    # If date_filed exists and is a string, use it. Otherwise, default to today.
                    raw_date = f_row['date_filed']
                    if isinstance(raw_date, str) and raw_date != 'None' and raw_date != '':
                        try:
                            current_d = datetime.strptime(raw_date, '%Y-%m-%d').date()
                        except:
                            current_d = datetime.now().date()
                    else:
                        current_d = datetime.now().date()
                    
                    c1, c2, c3, c4, c5 = st.columns([1, 1, 1.5, 1, 1])
                    c1.write(f"**{m}**")
                    c2.write("✅ Filed" if is_f else "❌ Not Filed")
                    
                    # This date input allows you to alter the date before or after filing
                    selected_date = c3.date_input("Filing Date", value=current_d, key=f"date_{m}", label_visibility="collapsed")
                    
                    if not is_f:
                        if c4.button(f"Mark Filed", key=f"f_{m}"):
                            # Filters by the start and end of the month string (e.g., '2023-10')
                            supabase.table("logs").update({
                                "is_filed": True, 
                                "date_filed": selected_date.strftime('%Y-%m-%d')
                            }).filter("date_field", "gte", f"{m}-01").filter("date_field", "lte", f"{m}-31").execute()
                            st.rerun()
                    else:
                        # NEW: Alter the date for an already filed month
                        if c4.button(f"Update Date", key=f"up_{m}"):
                            supabase.table("logs").update({
                                "date_filed": selected_date.strftime('%Y-%m-%d')
                            }).filter("date_field", "gte", f"{m}-01").filter("date_field", "lte", f"{m}-31").execute()
                            st.success(f"Filing date for {m} updated!")
                            st.rerun()
                            
                        if c5.button(f"Unmark", key=f"u_{m}"):
                            supabase.table("logs").update({
                                "is_filed": False, 
                                "date_filed": None
                            }).filter("date_field", "gte", f"{m}-01").filter("date_field", "lte", f"{m}-31").execute()
                            st.rerun()

# ... (Keep the rest of your Itemized Sales & Override logic)
