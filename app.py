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
from PIL import Image
import PyPDF2

# --- CONFIGURATION ---
st.set_page_config(page_title="Jarvis Ultimate", layout="wide", page_icon="🛡️")

# 1. Setup Database & Google Services
if "FIREBASE_KEY" in st.secrets:
    try:
        key_dict = json.loads(st.secrets["FIREBASE_KEY"]) if isinstance(st.secrets["FIREBASE_KEY"], str) else dict(st.secrets["FIREBASE_KEY"])
        
        # Firebase
        try: app = firebase_admin.get_app()
        except ValueError: 
            cred = credentials.Certificate(key_dict)
            app = firebase_admin.initialize_app(cred)
        db = firestore.client()
        
        # Google Calendar & Drive
        SCOPES = ['https://www.googleapis.com/auth/calendar', 'https://www.googleapis.com/auth/drive']
        creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
        cal_service = build('calendar', 'v3', credentials=creds)
        drive_service = build('drive', 'v3', credentials=creds)
        
    except Exception as e: 
        db = None; cal_service = None; drive_service = None
        st.error(f"Service Error: {e}")
else:
    db = None

# CALENDAR EMAIL (The calendar you want to edit)
CALENDAR_EMAIL = 'mybusiness110010@gmail.com' 

# 2. Setup Brain
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
model = genai.GenerativeModel('models/gemini-2.0-flash')

# --- UTILS ---
def clean_html(raw_html):
    """Remove HTML tags to let AI read clean text"""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return cleantext[:1000] # Limit to 1000 chars per email to save context

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

# --- ENHANCED EMAIL TOOLS ---
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
    """Reads Subject + Body + Links"""
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
                    
                    # Decode Subject
                    subject, encoding = decode_header(msg["subject"])[0]
                    if isinstance(subject, bytes): 
                        subject = subject.decode(encoding if encoding else "utf-8")
                    
                    sender = msg["from"]
                    
                    # Extract Body
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode()
                                break
                            elif part.get_content_type() == "text/html":
                                html_body = part.get_payload(decode=True).decode()
                                body = clean_html(html_body) # Strip tags
                    else:
                        body = msg.get_payload(decode=True).decode()
                    
                    result.append(f"📩 FROM: {sender}\nSUBJECT: {subject}\nCONTENT: {body[:600]}...\n")
        
        mail.close()
        mail.logout()
        return "\n".join(result)
    except Exception as e: return f"Read Email Error: {e}"

# --- CALENDAR TOOLS (FIXED) ---
def get_calendar_events():
    if not cal_service: return "Calendar disconnected."
    try:
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events = cal_service.events().list(calendarId=CALENDAR_EMAIL, timeMin=now, maxResults=5, singleEvents=True, orderBy='startTime').execute().get('items', [])
        if not events: return "No events."
        return "\n".join([f"📅 {e['start'].get('dateTime', e['start'].get('date'))}: {e['summary']}" for e in events])
    except: return "Calendar Error"

def add_calendar_event(summary, start_time_str):
    if not cal_service: return "Calendar disconnected."
    try:
        if "T" in start_time_str:
             start_dt = datetime.datetime.fromisoformat(start_time_str)
        else:
             # Fallback logic for various time formats
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

# --- DRIVE TOOLS ---
def save_to_drive(filename, content):
    if not drive_service: return "Drive not connected."
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
    if not drive_service: return "Drive not connected."
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
        audio_file.seek(0)
        prompt = "Transcribe exactly."
        response = model.generate_content([prompt, {"mime_type": "audio/wav", "data": audio_file.read()}])
        return response.text
    except: return "Error listening."

async def speak(text):
    communicate = edge_tts.Communicate(text, "en-IN-NeerjaNeural")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
        await communicate.save(fp.name)
        return fp.name

def ask_gemini(prompt_parts):
    try:
        return model.generate_content(prompt_parts).text
    except Exception as e: return f"Error: {e}"

# --- UI LAYOUT ---
st.title("🛡️ Jarvis Ultimate Agent")

with st.sidebar:
    st.header("📧 Communication")
    if st.button("Deep Read Inbox"):
        with st.spinner("Analyzing Emails..."): st.info(read_emails_deep())
    st.divider()
    st.header("📅 Calendar")
    if st.button("Refresh Events"): st.rerun()
    st.text(get_calendar_events())
    st.divider()
    st.header("📂 Drive (Ready)")
    if st.button("List Files"): st.info(list_drive_files())

# --- CHAT LOGIC ---
if "messages" not in st.session_state: st.session_state.messages = []
if "last_audio" not in st.session_state: st.session_state.last_audio = None

audio_value = st.audio_input("🎙️ Voice Command")
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]): st.write(msg["content"])
user_text = st.chat_input("Type instruction...")

final_input = None
if audio_value and audio_value != st.session_state.last_audio:
    st.session_state.last_audio = audio_value
    with st.spinner("Processing Voice..."):
        final_input = transcribe_audio(audio_value)
elif user_text:
    final_input = user_text

if final_input:
    st.session_state.messages.append({"role": "user", "content": final_input})
    with st.chat_message("user"): st.write(final_input)

    memories = get_memories()
    calendar_data = get_calendar_events()
    
    # --- PROMPT ---
    sys_prompt = f"""
    SYSTEM: You are Jarvis, a Functional AI Agent.
    
    CAPABILITIES:
    - SCHEDULE MEETINGS (Use 'schedule' tool).
    - READ EMAIL DEEP (Use 'read_email' tool).
    - SEND EMAILS.
    - SAVE TO DRIVE.
    
    MEMORIES: {memories}
    CALENDAR: {calendar_data}
    DATE: {datetime.datetime.now().strftime("%Y-%m-%d")}
    
    INSTRUCTIONS:
    1. If user asks to read email, use 'read_email'. I will give you the full content. 
       THEN you must summarize it and extract links for the user.
    2. If user asks to schedule, use 'schedule'.
    
    TOOLS (OUTPUT JSON ONLY):
    - Search -> {{"action": "search", "query": "..."}}
    - Videos -> {{"action": "search_video", "query": "..."}}
    - Email -> {{"action": "send_email", "to": "...", "subject": "...", "body": "..."}}
    - Read Email -> {{"action": "read_email"}}
    - Schedule -> {{"action": "schedule", "summary": "Meeting Name", "time": "2025-01-20T10:00:00"}}
    - Save to Drive -> {{"action": "save_drive", "filename": "...", "content": "..."}}
    - Save Memory -> {{"action": "save_memory", "text": "..."}}
    """
    
    reply = ask_gemini([sys_prompt, f"USER: {final_input}"])
    
    final_response = reply
    if "{" in reply and "action" in reply:
        try:
            start = reply.find("{")
            end = reply.rfind("}") + 1
            data = json.loads(reply[start:end])
            
            if data["action"] == "search":
                with st.status(f"🔎 Researching: {data['query']}...", expanded=True) as status:
                    res = google_search(data["query"])
                    status.write("Found info...")
                    research_prompt = f"{sys_prompt}\nDATA:{res}\nUSER:{final_input}\nINSTRUCTION: Answer and CITE."
                    final_response = ask_gemini(research_prompt)
                    status.update(label="✅ Done", state="complete", expanded=False)

            elif data["action"] == "search_video":
                 with st.status("🎥 Searching YouTube...", expanded=True) as status:
                    res = search_youtube(data["query"])
                    final_response = f"Found Videos:\n\n{res}"
                    status.update(label="✅ Done", state="complete", expanded=False)

            elif data["action"] == "send_email":
                with st.status(f"📧 Sending to {data['to']}...", expanded=True) as status:
                    final_response = send_email(data["to"], data["subject"], data["body"])
                    status.update(label="✅ Sent!", state="complete", expanded=False)

            elif data["action"] == "read_email":
                with st.status("📧 Analyzing Inbox...", expanded=True) as status:
                    raw_emails = read_emails_deep()
                    status.write("Summarizing...")
                    # FEED RAW EMAILS BACK TO BRAIN FOR SUMMARY
                    analysis_prompt = f"""
                    {sys_prompt}
                    RAW EMAIL DATA:
                    {raw_emails}
                    
                    USER INSTRUCTION: Summarize these emails. 
                    - Tell me what is useful.
                    - List any Important Links found.
                    - Ignore spam.
                    """
                    final_response = ask_gemini(analysis_prompt)
                    status.update(label="✅ Analysis Complete", state="complete", expanded=False)

            elif data["action"] == "schedule":
                with st.status(f"📅 Scheduling {data['summary']}...", expanded=True) as status:
                    final_response = add_calendar_event(data["summary"], data["time"])
                    status.update(label="✅ Scheduled!", state="complete", expanded=False)

            elif data["action"] == "save_drive":
                with st.status(f"💾 Saving {data['filename']}...", expanded=True) as status:
                    final_response = save_to_drive(data["filename"], data["content"])
                    status.update(label="✅ Saved!", state="complete", expanded=False)

            elif data["action"] == "save_memory":
                add_memory(data["text"])
                final_response = "🧠 Memory Saved."
                
        except Exception as e:
            final_response = f"Tool Error: {e}"

    with st.chat_message("assistant"):
        st.write(final_response)
        if len(final_response) < 400:
             try:
                audio = asyncio.run(speak(final_response.replace("*", "").replace("http", "")))
                st.audio(audio, autoplay=True)
             except: pass
    
    st.session_state.messages.append({"role": "assistant", "content": final_response})
