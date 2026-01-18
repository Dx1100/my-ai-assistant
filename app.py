import streamlit as st
import google.generativeai as genai
import firebase_admin
from firebase_admin import credentials, firestore
import edge_tts
import asyncio
import json
import tempfile
import datetime
import smtplib
import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
import PyPDF2

# --- CONFIGURATION ---
st.set_page_config(page_title="Jarvis Pro", layout="wide", page_icon="📅")

# --- 1. SETUP DATABASE (FIREBASE KEY ONLY) ---
if "FIREBASE_KEY" in st.secrets:
    try:
        key_dict = json.loads(st.secrets["FIREBASE_KEY"]) if isinstance(st.secrets["FIREBASE_KEY"], str) else dict(st.secrets["FIREBASE_KEY"])
        try: app = firebase_admin.get_app()
        except ValueError: 
            cred = credentials.Certificate(key_dict)
            app = firebase_admin.initialize_app(cred)
        db = firestore.client()
    except Exception as e: 
        db = None
        st.error(f"Database Error: {e}")
else:
    db = None

# --- 2. SETUP CALENDAR (USE THE ORIGINAL CALENDAR KEY) ---
# We switch back to GOOGLE_CALENDAR_KEY because we know this one works for you.
SCOPES = ['https://www.googleapis.com/auth/calendar']
cal_service = None
CALENDAR_EMAIL = 'mybusiness110010@gmail.com' 

if "GOOGLE_CALENDAR_KEY" in st.secrets:
    try:
        cal_info = st.secrets["GOOGLE_CALENDAR_KEY"]
        if isinstance(cal_info, str): cal_creds_dict = json.loads(cal_info)
        else: cal_creds_dict = dict(cal_info)
        
        cal_creds = service_account.Credentials.from_service_account_info(cal_creds_dict, scopes=SCOPES)
        cal_service = build('calendar', 'v3', credentials=cal_creds)
    except Exception as e:
        st.error(f"Calendar Key Error: {e}")

# --- 3. SETUP DRIVE (TRY FIREBASE KEY, IF FAIL, IGNORE FOR NOW) ---
# We will deal with Drive later. This prevents it from crashing the Calendar.
drive_service = None
if "FIREBASE_KEY" in st.secrets:
    try:
        key_dict = json.loads(st.secrets["FIREBASE_KEY"]) if isinstance(st.secrets["FIREBASE_KEY"], str) else dict(st.secrets["FIREBASE_KEY"])
        drive_scopes = ['https://www.googleapis.com/auth/drive']
        drive_creds = service_account.Credentials.from_service_account_info(key_dict, scopes=drive_scopes)
        drive_service = build('drive', 'v3', credentials=drive_creds)
    except: pass

# --- 4. SETUP BRAIN ---
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
model = genai.GenerativeModel('models/gemini-2.0-flash')

# --- UTILS ---
def clean_html(raw_html):
    cleanr = re.compile('<.*?>')
    return re.sub(cleanr, '', raw_html)[:1000]

# --- TOOLS ---

def google_search(query):
    if "GOOGLE_SEARCH_KEY" not in st.secrets: return "Error: No Search Keys"
    try:
        service = build("customsearch", "v1", developerKey=st.secrets["GOOGLE_SEARCH_KEY"])
        result = service.cse().list(q=query, cx=st.secrets["GOOGLE_SEARCH_CX"], num=5).execute()
        items = result.get('items', [])
        if not items: return "No results."
        return "\n".join([f"Source: {i['title']}\nURL: {i['link']}\nSnippet: {i['snippet']}\n" for i in items])
    except Exception as e: return f"Search Failed: {e}"

def search_youtube(query):
    if "GOOGLE_SEARCH_KEY" not in st.secrets: return "Error: No Search Keys"
    try:
        youtube_query = f"{query} site:youtube.com"
        service = build("customsearch", "v1", developerKey=st.secrets["GOOGLE_SEARCH_KEY"])
        result = service.cse().list(q=youtube_query, cx=st.secrets["GOOGLE_SEARCH_CX"], num=3).execute()
        items = result.get('items', [])
        if not items: return "No videos found."
        return "\n".join([f"🎥 Video: {i['title']}\n🔗 Link: {i['link']}\n" for i in items])
    except Exception as e: return f"Video Search Failed: {e}"

def send_email(to_email, subject, body):
    if "GMAIL_USER" not in st.secrets: return "Error: Gmail secrets missing."
    try:
        msg = MIMEMultipart()
        msg['From'] = st.secrets["GMAIL_USER"]
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(st.secrets["GMAIL_USER"], st.secrets["GMAIL_PASSWORD"])
        server.send_message(msg)
        server.quit()
        return f"✅ Email sent to {to_email}"
    except Exception as e: return f"Email Failed: {e}"

def read_emails_deep(limit=3):
    if "GMAIL_USER" not in st.secrets: return "Error: Gmail secrets missing."
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(st.secrets["GMAIL_USER"], st.secrets["GMAIL_PASSWORD"])
        mail.select("inbox")
        status, messages = mail.search(None, '(UNSEEN)')
        email_ids = messages[0].split()[-limit:]
        if not email_ids: return "No new unread emails."
        result = []
        for e_id in email_ids:
            _, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    subject, encoding = decode_header(msg["subject"])[0]
                    if isinstance(subject, bytes): subject = subject.decode(encoding if encoding else "utf-8")
                    sender = msg["from"]
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode()
                                break
                            elif part.get_content_type() == "text/html":
                                body = clean_html(part.get_payload(decode=True).decode())
                    else:
                        body = msg.get_payload(decode=True).decode()
                    result.append(f"📩 FROM: {sender}\nSUBJECT: {subject}\nCONTENT: {body[:600]}...\n")
        mail.close()
        mail.logout()
        return "\n".join(result)
    except Exception as e: return f"Read Email Error: {e}"

def get_calendar_events():
    if not cal_service: return "Calendar disconnected."
    try:
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events = cal_service.events().list(calendarId=CALENDAR_EMAIL, timeMin=now, maxResults=5, singleEvents=True, orderBy='startTime').execute().get('items', [])
        if not events: return "No events."
        return "\n".join([f"📅 {e['start'].get('dateTime', e['start'].get('date'))}: {e['summary']}" for e in events])
    except Exception as e: return f"Calendar Error: {e}"

def add_calendar_event(summary, start_time_str):
    if not cal_service: return "Calendar disconnected."
    try:
        if "T" in start_time_str:
             start_dt = datetime.datetime.fromisoformat(start_time_str)
        else:
             start_dt = datetime.datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S")
        end_dt = start_dt + datetime.timedelta(hours=1)
        event = {
            'summary': summary,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Kolkata'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Asia/Kolkata'},
        }
        cal_service.events().insert(calendarId=CALENDAR_EMAIL, body=event).execute()
        return f"✅ Scheduled '{summary}' for {start_time_str}"
    except Exception as e: return f"Scheduling Failed: {e}"

def save_to_drive(filename, content):
    if not drive_service: return "Drive disconnected. (API might be disabled)"
    try:
        folder_id = None
        results = drive_service.files().list(q="name='Jarvis_Memory' and mimeType='application/vnd.google-apps.folder'", fields="files(id, name)").execute()
        items = results.get('files', [])
        if items: folder_id = items[0]['id']
        file_metadata = {'name': filename}; 
        if folder_id: file_metadata['parents'] = [folder_id]
        media = MediaIoBaseUpload(io.BytesIO(content.encode('utf-8')), mimetype='text/plain')
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return f"✅ Saved '{filename}' to Drive."
    except Exception as e: return f"Drive Save Error: {e}"

def list_drive_files():
    if not drive_service: return "Drive disconnected."
    try:
        results = drive_service.files().list(q="name='Jarvis_Memory' and mimeType='application/vnd.google-apps.folder'", fields="files(id, name)").execute()
        items = results.get('files', [])
        if not items: return "Folder 'Jarvis_Memory' not found."
        folder_id = items[0]['id']
        files_res = drive_service.files().list(q=f"'{folder_id}' in parents", fields="files(id, name)").execute()
        files = files_res.get('files', [])
        return "\n".join([f"📄 {f['name']}" for f in files]) if files else "Empty Folder."
    except Exception as e: return f"List Error: {e}"

# --- MEMORY & AUDIO ---
def get_memories():
    if not db: return []
    try:
        docs = db.collection('memories').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(5).stream()
        return [doc.to_dict().get('text') for doc in docs]
    except: return []

def add_memory(text):
    if db: db.collection('memories').add({'text': text, 'timestamp': firestore.SERVER_TIMESTAMP})

def transcribe_audio(audio_file):
    try:
        audio_file.seek(
