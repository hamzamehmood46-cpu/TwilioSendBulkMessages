import io
import os
import re
import time
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / "twilio.env")

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
PHONE_RE = re.compile(r"^\+\d{8,15}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CODE_RE = re.compile(r"^\d{6}$")
COUNTRY_CODES = ["+1", "+44", "+91", "+92", "+61", "+971", "+81", "+86", "+49", "+33"]

st.set_page_config(
    page_title="Twilio SMS Console",
    page_icon="\U0001F4F1",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------
if st.session_state.dark_mode:
    _bg, _secondary_bg, _text, _border = "#0e1117", "#1c1f26", "#fafafa", "rgba(255,255,255,0.18)"
else:
    _bg, _secondary_bg, _text, _border = "#ffffff", "#f0f2f6", "#1a1a1a", "rgba(0,0,0,0.12)"

st.markdown(
    f"""
    <style>
        :root {{
            --background-color: {_bg};
            --secondary-background-color: {_secondary_bg};
            --text-color: {_text};
            --primary-color: #F22F46;
        }}

        .stApp {{ background-color: {_bg}; color: {_text}; }}
        [data-testid="stSidebar"] {{ background-color: {_secondary_bg}; }}
        [data-testid="stExpander"] {{ border-color: {_border}; }}

        .stApp p,
        .stApp span:not(.auth-badge):not(.pill),
        .stApp label,
        .stApp h1, .stApp h2, .stApp h3, .stApp h4,
        .stApp li,
        .stApp [data-testid="stMarkdownContainer"],
        .stApp [data-testid="stWidgetLabel"],
        .stApp [data-testid="stCaptionContainer"],
        .stApp [data-testid="stMetricValue"],
        .stApp [data-testid="stMetricLabel"] {{
            color: {_text} !important;
        }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <style>
        html, body, [class*="css"] { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }

        #MainMenu, header, footer { visibility: hidden; }

        .block-container { padding-top: 2rem; max-width: 1100px; }

        .app-header {
            display: flex;
            align-items: center;
            gap: 14px;
            margin-bottom: 4px;
        }
        .app-header .logo {
            width: 42px; height: 42px;
            background: linear-gradient(135deg, #F22F46, #CF1F36);
            border-radius: 12px;
            display: flex; align-items: center; justify-content: center;
            font-size: 22px;
            color: white;
            flex-shrink: 0;
        }
        .app-header h1 { font-size: 1.6rem; font-weight: 700; margin: 0; }
        .app-subtitle { color: #8a8f98; font-size: 0.95rem; margin: 2px 0 22px 0; }

        .pill {
            display: inline-block;
            background: #F22F4615;
            color: #F22F46;
            border-radius: 999px;
            padding: 3px 12px;
            font-size: 0.8rem;
            font-weight: 600;
            margin-bottom: 10px;
        }

        .auth-badge {
            display: inline-block;
            background: #16a34a18;
            color: #16a34a;
            border-radius: 999px;
            padding: 4px 12px;
            font-size: 0.8rem;
            font-weight: 600;
        }

        .stButton>button[kind="primary"] {
            background: #F22F46;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            padding: 0.55rem 1.4rem;
        }
        .stButton>button[kind="primary"]:hover { background: #CF1F36; }

        .result-row {
            display: flex; justify-content: space-between; align-items: center;
            padding: 8px 12px; border-radius: 8px; margin-bottom: 6px;
            font-size: 0.9rem;
        }
        .result-sent { background: #16a34a18; color: #16a34a; }
        .result-failed { background: #dc262618; color: #dc2626; }

        .msg-bubble {
            background: #F22F46;
            color: white;
            border-radius: 16px 16px 4px 16px;
            padding: 12px 16px;
            max-width: 320px;
            margin-left: auto;
            font-size: 0.92rem;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        .msg-empty { color: #8a8f98; font-style: italic; }

        .lock-wrap {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            padding: 80px 20px;
            color: #8a8f98;
        }
        .lock-wrap .lock-icon { font-size: 2.6rem; margin-bottom: 14px; }
        .lock-wrap h2 { color: inherit; font-size: 1.2rem; margin: 0 0 6px 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="app-header">
        <div class="logo">\U0001F4AC</div>
        <h1>Twilio SMS Console</h1>
    </div>
    <div class="app-subtitle">Send announcements to department contacts and manage your contact list.</div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
if "auth_token" not in st.session_state:
    st.session_state.auth_token = None
    st.session_state.auth_expires_at = 0


def is_authenticated() -> bool:
    return bool(st.session_state.auth_token) and st.session_state.auth_expires_at > time.time()


def auth_headers() -> dict:
    return {"Authorization": f"Bearer {st.session_state.auth_token}"} if is_authenticated() else {}


def log_out():
    try:
        requests.post(f"{API_BASE_URL}/api/auth/logout", headers=auth_headers(), timeout=5)
    except requests.RequestException:
        pass
    st.session_state.auth_token = None
    st.session_state.auth_expires_at = 0


def handle_unauthorized(resp: requests.Response) -> bool:
    """If the backend says the session is invalid/expired, clear it locally. Returns True if it was a 401."""
    if resp.status_code == 401:
        st.session_state.auth_token = None
        st.session_state.auth_expires_at = 0
        return True
    return False


# ---------------------------------------------------------------------------
# Sidebar — authenticator login (always visible, even before login)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.toggle("\U0001F319 Dark mode", key="dark_mode")
    st.divider()

    st.markdown("### Authentication")

    if is_authenticated():
        remaining = int(st.session_state.auth_expires_at - time.time())
        st.markdown('<span class="auth-badge">✓ Authenticated</span>', unsafe_allow_html=True)
        st.caption(f"Session expires in {remaining // 60}m {remaining % 60}s")
        if st.button("Log out", use_container_width=True):
            log_out()
            st.rerun()
    else:
        st.caption("Open your authenticator app (Google Authenticator, Authy, etc.) and enter the current 6-digit code.")
        with st.form("login_form"):
            code = st.text_input("Authenticator code", max_chars=6, placeholder="123456", label_visibility="collapsed")
            login_clicked = st.form_submit_button("Verify & Log In", type="primary", use_container_width=True)

        if login_clicked:
            if not CODE_RE.match(code.strip()):
                st.error("Enter a 6-digit code.")
            else:
                try:
                    resp = requests.post(f"{API_BASE_URL}/api/auth/login", json={"code": code.strip()}, timeout=5)
                    if resp.status_code != 200:
                        st.error(resp.json().get("detail", "Login failed"))
                    else:
                        data = resp.json()
                        st.session_state.auth_token = data["token"]
                        st.session_state.auth_expires_at = time.time() + data["expiresInSeconds"]
                        st.rerun()
                except requests.RequestException as err:
                    st.error(f"Request failed: {err}")

# ---------------------------------------------------------------------------
# Gate: don't render or load anything else until logged in
# ---------------------------------------------------------------------------
if not is_authenticated():
    st.markdown(
        """
        <div class="lock-wrap">
            <div class="lock-icon">\U0001F512</div>
            <h2>Authentication required</h2>
            <p>Enter the 6-digit code from your authenticator app in the sidebar to continue.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

# ---------------------------------------------------------------------------
# Data helpers (cached so we only hit the backend when something changes)
# ---------------------------------------------------------------------------


@st.cache_data(ttl=30, show_spinner=False)
def fetch_contacts():
    resp = requests.get(f"{API_BASE_URL}/api/contacts", timeout=5)
    resp.raise_for_status()
    return resp.json()


def refresh_contacts():
    fetch_contacts.clear()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_phone_numbers():
    resp = requests.get(f"{API_BASE_URL}/api/phone-numbers", timeout=5)
    resp.raise_for_status()
    return resp.json()


def filter_groups(all_groups: dict, query: str) -> dict:
    query = query.strip().lower()
    if not query:
        return all_groups
    filtered = {}
    for group_name, contacts in all_groups.items():
        matches = [
            c for c in contacts
            if query in c["name"].lower()
            or query in c["phone"].lower()
            or query in c.get("email", "").lower()
        ]
        if matches:
            filtered[group_name] = matches
    return filtered


try:
    groups = fetch_contacts()
    contacts_error = None
except requests.RequestException as err:
    groups = {}
    contacts_error = str(err)

try:
    phone_numbers = fetch_phone_numbers()
    phone_numbers_error = None
except requests.RequestException as err:
    phone_numbers = []
    phone_numbers_error = str(err)

with st.sidebar:
    st.divider()
    st.metric("Groups", len(groups))
    st.metric("Total contacts", sum(len(v) for v in groups.values()))

if contacts_error:
    st.error(f"Could not reach the backend at {API_BASE_URL}: {contacts_error}")
    st.stop()

tab_send, tab_contacts, tab_logs = st.tabs(["\U0001F4E4  Send Message", "\U0001F465  Manage Contacts", "\U0001F4CB  Message Logs"])

# ---------------------------------------------------------------------------
# Send Message tab
# ---------------------------------------------------------------------------
with tab_send:
    col_recipients, col_compose = st.columns([1, 1], gap="large")

    selected_recipients = []

    with col_recipients:
        with st.container(border=True):
            st.markdown("### Recipients")
            if not groups:
                st.info("No contacts yet — add some in the Manage Contacts tab.")

            search_query = st.text_input(
                "Search contacts",
                key="recipient_search",
                placeholder="Search by name, phone, or email",
                label_visibility="collapsed",
            )
            visible_groups = filter_groups(groups, search_query)
            if search_query and not visible_groups:
                st.caption("No contacts match your search.")

            def _make_select_all_callback(group_name, contacts):
                def _callback():
                    new_value = st.session_state[f"group_{group_name}"]
                    for contact in contacts:
                        st.session_state[f"contact_{group_name}_{contact['phone']}"] = new_value
                return _callback

            for group_name, contacts in visible_groups.items():
                with st.expander(f"{group_name} ({len(contacts)})", expanded=bool(search_query)):
                    st.checkbox(
                        "Select all in this group",
                        key=f"group_{group_name}",
                        on_change=_make_select_all_callback(group_name, contacts),
                    )
                    for contact in contacts:
                        label = f"{contact['name']} — {contact['phone']}"
                        if contact.get("email"):
                            label += f" — {contact['email']}"
                        st.checkbox(label, key=f"contact_{group_name}_{contact['phone']}")

            # Read selections from session_state over the full (unfiltered) contact list,
            # so a contact stays selected even if it's hidden by the current search.
            for group_name, contacts in groups.items():
                for contact in contacts:
                    if st.session_state.get(f"contact_{group_name}_{contact['phone']}"):
                        selected_recipients.append(contact)

    with col_compose:
        with st.container(border=True):
            st.markdown("### Compose")

            from_number = None
            if phone_numbers_error:
                st.error(f"Could not load Twilio numbers: {phone_numbers_error}")
            elif not phone_numbers:
                st.warning("No Twilio numbers found on this account.")
            else:
                number_options = [f"{n['friendlyName']} ({n['phoneNumber']})" for n in phone_numbers]
                selected_label = st.selectbox("Send from", options=number_options, key="from_number_select")
                from_number = phone_numbers[number_options.index(selected_label)]["phoneNumber"]

            message = st.text_area("Message", max_chars=480, height=130, label_visibility="collapsed", placeholder="Type your message...")
            count_color = "#dc2626" if len(message) > 420 else "#8a8f98"
            st.markdown(f"<span style='color:{count_color};font-size:0.8rem'>{len(message)} / 480</span>", unsafe_allow_html=True)

            st.markdown("**Preview**")
            if message.strip():
                st.markdown(f'<div class="msg-bubble">{message}</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="msg-empty">Your message preview will appear here.</div>', unsafe_allow_html=True)

            st.write("")
            st.caption(f"{len(selected_recipients)} recipient(s) selected")
            send_clicked = st.button("Send Message", type="primary", use_container_width=True)

        if send_clicked:
            if not from_number:
                st.error("No sender number available — check your Twilio account.")
            elif not message.strip():
                st.error("Please enter a message.")
            elif not selected_recipients:
                st.error("Please select at least one recipient.")
            else:
                with st.spinner("Queuing..."):
                    try:
                        resp = requests.post(
                            f"{API_BASE_URL}/api/send",
                            json={"message": message, "fromNumber": from_number, "recipients": selected_recipients},
                            headers=auth_headers(),
                            timeout=30,
                        )
                        data = resp.json()
                    except requests.RequestException as err:
                        st.error(f"Request failed: {err}")
                        data = None

                if data is not None:
                    if handle_unauthorized(resp):
                        st.error("Session expired. Please log in again.")
                        st.rerun()
                    elif resp.status_code != 200:
                        st.error(data.get("detail", "Send failed"))
                    else:
                        # The backend just enqueues the batch on a Celery worker and
                        # returns immediately; poll for the result instead of blocking
                        # the whole request on however many Twilio calls are in the batch.
                        task_id = data["taskId"]
                        with st.spinner(f"Sending to {data['total']} recipient(s)..."):
                            status_data = None
                            for _ in range(60):
                                try:
                                    status_resp = requests.get(
                                        f"{API_BASE_URL}/api/send/status/{task_id}",
                                        headers=auth_headers(),
                                        timeout=10,
                                    )
                                    status_data = status_resp.json()
                                except requests.RequestException as err:
                                    st.error(f"Request failed: {err}")
                                    break
                                if status_resp.status_code != 200:
                                    st.error(status_data.get("detail", "Send failed"))
                                    break
                                if status_data.get("status") == "done":
                                    break
                                time.sleep(1)

                        if status_data and status_data.get("status") == "done":
                            st.success(f"Message sent to {status_data['sentCount']} recipient(s).")
                            failed_count = status_data["total"] - status_data["sentCount"]
                            if failed_count:
                                st.warning(f"{failed_count} message(s) failed to send.")
                        elif status_data and status_data.get("status") != "done":
                            st.warning("Still sending in the background — check back in a moment.")

# ---------------------------------------------------------------------------
# Manage Contacts tab
# ---------------------------------------------------------------------------
with tab_contacts:
    col_list, col_add = st.columns([1.2, 1], gap="large")

    with col_list:
        with st.container(border=True):
            st.markdown("### Current Contacts")
            if not groups:
                st.info("No contacts yet.")

            contacts_search = st.text_input(
                "Search contacts",
                key="contacts_search",
                placeholder="Search by name, phone, or email",
                label_visibility="collapsed",
            )
            visible_contact_groups = filter_groups(groups, contacts_search)
            if contacts_search and not visible_contact_groups:
                st.caption("No contacts match your search.")

            for group_name, contacts in visible_contact_groups.items():
                with st.expander(f"{group_name} ({len(contacts)})", expanded=bool(contacts_search)):
                    if not contacts:
                        st.caption("No contacts in this group.")
                    for contact in contacts:
                        c1, c2 = st.columns([5, 1])
                        with c1:
                            line = f"{contact['name']} — {contact['phone']}"
                            if contact.get("email"):
                                line += f" — {contact['email']}"
                            st.write(line)
                        with c2:
                            if st.button("Remove", key=f"del_{group_name}_{contact['phone']}", use_container_width=True):
                                try:
                                    resp = requests.delete(
                                        f"{API_BASE_URL}/api/contacts",
                                        json={"group": group_name, "phone": contact["phone"]},
                                        headers=auth_headers(),
                                        timeout=5,
                                    )
                                    if handle_unauthorized(resp):
                                        st.error("Session expired. Please log in again.")
                                    elif resp.status_code != 200:
                                        st.error(resp.json().get("detail", "Could not remove contact"))
                                    else:
                                        refresh_contacts()
                                    st.rerun()
                                except requests.RequestException as err:
                                    st.error(f"Request failed: {err}")

    with col_add:
        if "contact_form_version" not in st.session_state:
            st.session_state.contact_form_version = 0
        form_v = st.session_state.contact_form_version

        with st.container(border=True):
            st.markdown("### Add Contact")
            existing_groups = list(groups.keys())
            group_choice = st.selectbox(
                "Group", options=existing_groups + ["+ New group"], key=f"group_choice_{form_v}"
            )
            new_group_name = ""
            if group_choice == "+ New group":
                new_group_name = st.text_input("New group name", key=f"new_group_name_{form_v}")
            name = st.text_input("Name", key=f"contact_name_{form_v}")

            code_col, digits_col = st.columns([1, 3])
            with code_col:
                country_code = st.selectbox(
                    "Code", options=COUNTRY_CODES, key=f"contact_code_{form_v}", label_visibility="visible"
                )
            with digits_col:
                phone_digits = st.text_input(
                    "Phone number", placeholder="5551234567", key=f"contact_phone_{form_v}"
                )

            email = st.text_input("Email (optional)", placeholder="name@example.com", key=f"contact_email_{form_v}")
            submitted = st.button("Add Contact", type="primary", use_container_width=True, key=f"add_contact_btn_{form_v}")

        if submitted:
            target_group = new_group_name.strip() if group_choice == "+ New group" else group_choice
            digits_only = re.sub(r"\D", "", phone_digits)
            full_phone = f"{country_code}{digits_only}"
            if not target_group:
                st.error("Group name is required.")
            elif not name.strip():
                st.error("Name is required.")
            elif not digits_only or not PHONE_RE.match(full_phone):
                st.error("Enter a valid phone number (digits only, after the country code).")
            elif email.strip() and not EMAIL_RE.match(email.strip()):
                st.error("Enter a valid email address, or leave it blank.")
            else:
                try:
                    resp = requests.post(
                        f"{API_BASE_URL}/api/contacts",
                        json={
                            "group": target_group,
                            "name": name.strip(),
                            "phone": full_phone,
                            "email": email.strip(),
                        },
                        headers=auth_headers(),
                        timeout=5,
                    )
                    if handle_unauthorized(resp):
                        st.error("Session expired. Please log in again.")
                    elif resp.status_code != 200:
                        st.error(resp.json().get("detail", "Could not add contact"))
                    else:
                        refresh_contacts()
                        st.session_state.contact_form_version += 1
                        st.success(f"Added {name.strip()} to {target_group}.")
                        st.rerun()
                except requests.RequestException as err:
                    st.error(f"Request failed: {err}")

        if "bulk_import_version" not in st.session_state:
            st.session_state.bulk_import_version = 0
        bulk_v = st.session_state.bulk_import_version

        with st.container(border=True):
            st.markdown("### Bulk Import (Excel or CSV)")
            st.caption("Upload an .xlsx or .csv file with columns: Group, Name, Phone, Email (Email optional).")

            dl_col1, dl_col2 = st.columns(2)
            with dl_col1:
                template_buffer = io.BytesIO()
                pd.DataFrame([
                    {"Group": "IT Department", "Name": "Jane Doe", "Phone": "+15551234567", "Email": "jane@example.com"}
                ]).to_excel(template_buffer, index=False)
                st.download_button(
                    "Download template (.xlsx)",
                    data=template_buffer.getvalue(),
                    file_name="contacts_template.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            with dl_col2:
                csv_template = "Group,Name,Phone,Email\nIT Department,Jane Doe,+15551234567,jane@example.com\n"
                st.download_button(
                    "Download template (.csv)",
                    data=csv_template,
                    file_name="contacts_template.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

            uploaded_file = st.file_uploader(
                "Excel or CSV file", type=["xlsx", "csv"], key=f"bulk_upload_{bulk_v}", label_visibility="collapsed"
            )
            import_clicked = st.button(
                "Import Contacts",
                type="primary",
                use_container_width=True,
                key=f"bulk_import_btn_{bulk_v}",
                disabled=uploaded_file is None,
            )

        if import_clicked and uploaded_file is not None:
            try:
                if uploaded_file.name.lower().endswith(".csv"):
                    df = pd.read_csv(uploaded_file, dtype=str)
                else:
                    df = pd.read_excel(uploaded_file, dtype=str)
            except Exception as err:
                st.error(f"Could not read the file: {err}")
            else:
                df.columns = [str(c).strip().lower() for c in df.columns]
                missing = {"group", "name", "phone"} - set(df.columns)
                if missing:
                    st.error(f"Missing required column(s): {', '.join(sorted(missing))}")
                else:
                    def _cell(row, col):
                        val = row.get(col)
                        if val is None or (isinstance(val, float) and pd.isna(val)):
                            return ""
                        return str(val).strip()

                    rows = []
                    for _, row in df.iterrows():
                        row_name = _cell(row, "name")
                        row_phone = _cell(row, "phone")
                        if not row_name and not row_phone:
                            continue
                        rows.append({
                            "group": _cell(row, "group"),
                            "name": row_name,
                            "phone": row_phone,
                            "email": _cell(row, "email"),
                        })

                    if not rows:
                        st.warning("The file has no rows to import.")
                    else:
                        try:
                            resp = requests.post(
                                f"{API_BASE_URL}/api/contacts/bulk",
                                json={"contacts": rows},
                                headers=auth_headers(),
                                timeout=30,
                            )
                            data = resp.json()
                        except requests.RequestException as err:
                            st.error(f"Request failed: {err}")
                        else:
                            if handle_unauthorized(resp):
                                st.error("Session expired. Please log in again.")
                            elif resp.status_code != 200:
                                st.error(data.get("detail", "Bulk import failed"))
                            else:
                                refresh_contacts()
                                st.session_state.bulk_import_version += 1
                                st.success(f"Imported {data['addedCount']} contact(s).")
                                if data["errorCount"]:
                                    st.warning(f"{data['errorCount']} row(s) skipped.")
                                    for e in data["errors"]:
                                        st.caption(f"Row {e['row']} ({e.get('name', '')}): {e['reason']}")
                                st.rerun()

# ---------------------------------------------------------------------------
# Message Logs tab
# ---------------------------------------------------------------------------
with tab_logs:
    log_subtab_msg, log_subtab_auth = st.tabs(["SMS Logs", "Auth / Login Logs"])

    # ---- SMS Logs ----
    with log_subtab_msg:
        col_log_header, col_log_refresh = st.columns([4, 1])
        with col_log_header:
            st.markdown("### SMS Message Logs")
        with col_log_refresh:
            st.button("Refresh", key="refresh_logs", use_container_width=True)

        sms_limit = st.number_input("Show last N entries", min_value=10, max_value=500, value=100, step=10, key="sms_limit")

        try:
            logs_resp = requests.get(
                f"{API_BASE_URL}/api/logs",
                params={"limit": sms_limit},
                headers=auth_headers(),
                timeout=10,
            )
            if handle_unauthorized(logs_resp):
                st.error("Session expired. Please log in again.")
            elif logs_resp.status_code != 200:
                st.error(logs_resp.json().get("detail", "Could not fetch logs"))
            else:
                logs = logs_resp.json()
                if not logs:
                    st.info("No messages logged yet.")
                else:
                    sent_total = sum(1 for r in logs if r["status"] == "sent")
                    failed_total = len(logs) - sent_total
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Showing", len(logs))
                    m2.metric("Sent", sent_total)
                    m3.metric("Failed", failed_total)

                    st.divider()

                    for row in logs:
                        status_class = "result-sent" if row["status"] == "sent" else "result-failed"
                        status_label = row["status"].upper()
                        ts = row["sentAt"].replace("T", " ")[:19]
                        sid_text = f'SID: {row["twilioSid"]}' if row.get("twilioSid") else (row.get("error") or "")
                        st.markdown(
                            f"""
                            <div class="result-row {status_class}">
                                <span><strong>{row['recipientName']}</strong> &rarr; {row['toNumber']}</span>
                                <span style="font-size:0.82rem;opacity:0.8">{ts}</span>
                                <span><strong>{status_label}</strong></span>
                            </div>
                            <div style="font-size:0.78rem;color:#8a8f98;margin:-4px 0 10px 12px">
                                From: {row['fromNumber']} &nbsp;|&nbsp; {sid_text}
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )
        except requests.RequestException as err:
            st.error(f"Request failed: {err}")

    # ---- Auth / Login Logs ----
    with log_subtab_auth:
        col_auth_header, col_auth_refresh = st.columns([4, 1])
        with col_auth_header:
            st.markdown("### Authentication Logs")
        with col_auth_refresh:
            st.button("Refresh", key="refresh_auth_logs", use_container_width=True)

        auth_limit = st.number_input("Show last N entries", min_value=10, max_value=500, value=100, step=10, key="auth_limit")

        _ACTION_STYLE = {
            "login_success": ("result-sent", "LOGIN SUCCESS"),
            "login_failed": ("result-failed", "LOGIN FAILED"),
            "logout": ("", "LOGOUT"),
        }

        try:
            auth_resp = requests.get(
                f"{API_BASE_URL}/api/logs/auth",
                params={"limit": auth_limit},
                headers=auth_headers(),
                timeout=10,
            )
            if handle_unauthorized(auth_resp):
                st.error("Session expired. Please log in again.")
            elif auth_resp.status_code != 200:
                st.error(auth_resp.json().get("detail", "Could not fetch auth logs"))
            else:
                auth_logs = auth_resp.json()
                if not auth_logs:
                    st.info("No authentication events logged yet.")
                else:
                    success_count = sum(1 for r in auth_logs if r["action"] == "login_success")
                    failed_count = sum(1 for r in auth_logs if r["action"] == "login_failed")
                    logout_count = sum(1 for r in auth_logs if r["action"] == "logout")
                    a1, a2, a3 = st.columns(3)
                    a1.metric("Logins", success_count)
                    a2.metric("Failed Attempts", failed_count)
                    a3.metric("Logouts", logout_count)

                    st.divider()

                    for row in auth_logs:
                        css_class, label = _ACTION_STYLE.get(row["action"], ("", row["action"].upper()))
                        ts = row["loggedAt"].replace("T", " ")[:19]
                        ip_text = row.get("ipAddress") or "—"
                        detail_text = row.get("details") or ""
                        st.markdown(
                            f"""
                            <div class="result-row {css_class}">
                                <span><strong>{label}</strong></span>
                                <span style="font-size:0.82rem;opacity:0.8">{ts}</span>
                                <span>IP: {ip_text}</span>
                            </div>
                            {"" if not detail_text else f'<div style="font-size:0.78rem;color:#8a8f98;margin:-4px 0 10px 12px">{detail_text}</div>'}
                            """,
                            unsafe_allow_html=True,
                        )
        except requests.RequestException as err:
            st.error(f"Request failed: {err}")
