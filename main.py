import smtplib
import streamlit as st
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import tempfile
import os
import re
from typing import List, Optional
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# Load environment variables from .env file
load_dotenv()

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT_SSL = 465

# Load Gmail and MongoDB credentials from environment variables
try:
    GMAIL_USER = os.getenv("GMAIL_EMAIL")
    GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
    MONGODB_USERNAME = os.getenv("MONGODB_USERNAME")
    MONGODB_PASSWORD = os.getenv("MONGODB_PASSWORD")
    if not GMAIL_USER:
        st.error("GMAIL_EMAIL not found in .env file.")
        st.stop()
    if not GMAIL_APP_PASSWORD:
        st.error("GMAIL_APP_PASSWORD not found in .env file.")
        st.stop()
    if not MONGODB_USERNAME or not MONGODB_PASSWORD:
        st.error("MongoDB credentials (MONGODB_USERNAME or MONGODB_PASSWORD) not found in .env file.")
        st.stop()
except Exception as e:
    st.error(f"Error loading .env file: {str(e)}")
    st.stop()

# Initialize MongoDB client
try:
    MONGODB_URI = f"mongodb+srv://{MONGODB_USERNAME}:{MONGODB_PASSWORD}@cluster0.rw40wkt.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
    client = MongoClient(MONGODB_URI)
    db = client["email_app"]
    defaults_collection = db["defaults"]
    # Test connection
    client.admin.command("ping")
except ConnectionFailure:
    st.error("Failed to connect to MongoDB. Please check your credentials or network.")
    st.stop()

st.set_page_config(page_title="Gmail Sender", page_icon="📧", layout="centered")
st.title("📧 Streamlit Gmail Sender (App Password)")

# Initialize session state
for key, default in [
    ("default_subject", ""),
    ("default_body", ""),
    ("default_files", []),
    ("default_file_metadata", []),  # Store metadata (filename, size)
    ("tmp_paths", [])
]:
    if key not in st.session_state:
        st.session_state[key] = default

# Load defaults from MongoDB (single document for simplicity)
def load_defaults_from_mongo():
    try:
        defaults = defaults_collection.find_one({"user": GMAIL_USER})
        if defaults:
            st.session_state.default_subject = defaults.get("subject", "")
            st.session_state.default_body = defaults.get("body", "")
            st.session_state.default_file_metadata = defaults.get("file_metadata", [])
        else:
            # Initialize empty defaults in MongoDB
            defaults_collection.insert_one({
                "user": GMAIL_USER,
                "subject": "",
                "body": "",
                "file_metadata": []
            })
    except Exception as e:
        st.error(f"Error loading defaults from MongoDB: {str(e)}")

# Load defaults on app start
load_defaults_from_mongo()

# Email validation
def is_valid_email(email: str) -> bool:
    pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
    return bool(re.match(pattern, email))

# Save uploaded files to temporary storage
def save_uploaded_files_to_tmp(files: List[st.runtime.uploaded_file_manager.UploadedFile]) -> List[str]:
    paths = []
    max_size = 10 * 1024 * 1024  # 10MB limit per file
    for f in files:
        if f.size > max_size:
            st.warning(f"File '{f.name}' exceeds 10MB and will be skipped.")
            continue
        suffix = os.path.splitext(f.name)[1]
        tmp = tempfile.NamedTemporaryFile(prefix="stmail_", suffix=suffix, delete=False)
        tmp.write(f.getbuffer())
        tmp.flush()
        tmp.close()
        paths.append(tmp.name)
    return paths

# Build email message
def build_message(sender: str, recipients: List[str], subject: str, body: str, attachment_paths: List[str]) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject or "No Subject"
    msg.attach(MIMEText(body, "plain"))
    
    for path in attachment_paths:
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        filename = os.path.basename(path)
        part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
        msg.attach(part)
    return msg

# Send email via Gmail SSL
def send_via_gmail_ssl(sender: str, app_password: str, recipients: List[str], msg: MIMEMultipart) -> Optional[str]:
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT_SSL) as server:
            server.login(sender, app_password)
            server.sendmail(sender, recipients, msg.as_string())
        return None
    except smtplib.SMTPAuthenticationError:
        return "Authentication failed. Check your Gmail email and app password in .env."
    except smtplib.SMTPRecipientsRefused:
        return "One or more recipient emails are invalid."
    except Exception as e:
        return f"Failed to send email: {str(e)}"

# UI: Set default content
st.markdown("### Step 1 — Set default content")
with st.form("set_defaults"):
    d_subject = st.text_input("Default subject", value=st.session_state.default_subject, placeholder="e.g., Greetings")
    d_body = st.text_area("Default body", value=st.session_state.default_body, height=180, placeholder="Type your default email message here...")
    d_files = st.file_uploader("Default attachments (optional, max 10MB each)", accept_multiple_files=True, key="default_uploader")
    save_defaults = st.form_submit_button("Save defaults")

if save_defaults:
    # Save to session state
    st.session_state.default_subject = d_subject
    st.session_state.default_body = d_body
    st.session_state.default_files = d_files or []
    
    # Save file metadata (not the files themselves)
    file_metadata = [{"name": f.name, "size": f.size} for f in (d_files or [])]
    st.session_state.default_file_metadata = file_metadata
    
    # Save to MongoDB
    try:
        defaults_collection.update_one(
            {"user": GMAIL_USER},
            {"$set": {
                "subject": d_subject,
                "body": d_body,
                "file_metadata": file_metadata
            }},
            upsert=True
        )
        st.success("Default content saved to MongoDB.")
    except Exception as e:
        st.error(f"Error saving defaults to MongoDB: {str(e)}")

# Display saved attachment metadata
if st.session_state.default_file_metadata:
    st.markdown("**Saved default attachments**:")
    for meta in st.session_state.default_file_metadata:
        st.write(f"- {meta['name']} ({meta['size'] / 1024:.2f} KB)")

st.divider()
st.markdown("### Step 2 — Send email")
with st.form("send_email"):
    recipients_raw = st.text_input("Recipient email(s), comma-separated", placeholder="user1@example.com, user2@example.com")
    s_subject = st.text_input("Subject (can override default)", value=st.session_state.default_subject)
    s_body = st.text_area("Body (can override default)", value=st.session_state.default_body, height=200)
    more_files = st.file_uploader("Additional attachments (optional, max 10MB each)", accept_multiple_files=True, key="more_files")
    send_btn = st.form_submit_button("Send")

if send_btn:
    if not recipients_raw.strip():
        st.error("Please enter at least one recipient.")
    else:
        recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
        invalid_emails = [r for r in recipients if not is_valid_email(r)]
        if invalid_emails:
            st.error(f"Invalid email addresses: {', '.join(invalid_emails)}")
        else:
            tmp_paths = []
            try:
                # Save default and additional files
                if st.session_state.default_files:
                    tmp_paths.extend(save_uploaded_files_to_tmp(st.session_state.default_files))
                if more_files:
                    tmp_paths.extend(save_uploaded_files_to_tmp(more_files))
                
                # Build and send email
                msg = build_message(GMAIL_USER, recipients, s_subject, s_body, tmp_paths)
                error = send_via_gmail_ssl(GMAIL_USER, GMAIL_APP_PASSWORD, recipients, msg)
                if error:
                    st.error(error)
                else:
                    st.success("Email sent successfully.")
            finally:
                # Clean up temp files
                for p in tmp_paths:
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                    except Exception:
                        pass
