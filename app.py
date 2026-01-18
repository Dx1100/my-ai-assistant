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
from google.oauth2 import service_account
from googleapiclient.discovery import build
from PIL import Image
import PyPDF2

# --- CONFIGURATION ---
st.set_page_config(page_title="Jarvis Pro", layout="wide", page_icon="📧")

# 1. Setup Database
if "FIREBASE_KEY" in st.secrets:
    try:
        key_dict = json.loads(st.secrets["FIREBASE_KEY"]) if isinstance(st.secrets["FIREBASE_KEY"], str) else dict(st.secrets["FIREBASE_KEY"])
        try: app = firebase_admin.get_app()
        except ValueError: 
            cred = credentials.Certificate(key_dict)
            app = firebase_admin.initialize_app(cred)
        db = firestore.client()
    except: db = None
else: db = None

# 2. Setup Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']
cal_service = None
CALENDAR_EMAIL = 'mybusiness110010@gmail.com' 

if "GOOGLE_CALENDAR_KEY" in st.secrets:
    try:
        cal_info = st.secrets["GOOGLE_CALENDAR_KEY"]
        if isinstance(cal_info, str): cal_creds_dict = json.loads(cal_info)
        else: cal_creds_dict = dict(cal_info)
        creds = service_account.Credentials.from_service_account_info(cal_creds_dict, scopes=SCOPES)
        cal_service = build('calendar', 'v3', credentials=creds)
    except: pass

# 3. Setup Brain
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
model = genai.GenerativeModel('models/gemini-2.0-flash')

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

def read_emails(limit=5):
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
                    subject = email.header.decode_header(msg["subject"])[0][0]
                    if isinstance(subject, bytes): subject = subject.decode()
                    sender = msg["from"]
                    result.append(f"📩 From: {sender}\nSubject: {subject}\n")
        mail.close()
        mail.logout()
        return "\n".join(result)
    except Exception as e: return f"Read Email Error: {e}"

# --- CORE FUNCTIONS ---
def get_calendar_events():
    if not cal_service: return "Calendar disconnected."
    try:
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events = cal_service.events().list(calendarId=CALENDAR_EMAIL, timeMin=now, maxResults=5, singleEvents=True, orderBy='startTime').execute().get('items', [])
        if not events: return "No events."
        return "\n".join([f"📅 {e['start'].get('dateTime', e['start'].get('date'))}: {e['summary']}" for e in events])
    except: return "Calendar Error"

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
st.title("🤖 Jarvis Agent (Email Edition)")

with st.sidebar:
    st.header("📧 Email Agent")
    if st.button("Check Inbox"):
        with st.spinner("Reading Gmail..."):
            st.info(read_emails())
    
    st.divider()
    st.header("📅 Calendar")
    st.text(get_calendar_events())
    
    st.divider()
    st.header("🧠 Memory")
    if db:
        with st.expander("View Memories"):
            for m in get_memories(): st.info(m)

# --- CHAT LOGIC ---
if "messages" not in st.session_state: st.session_state.messages = []
if "last_audio" not in st.session_state: st.session_state.last_audio = None

# Inputs
audio_value = st.audio_input("🎙️ Voice Command")
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]): st.write(msg["content"])
user_text = st.chat_input("Type instruction...")

# Smart Switch Logic
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
    
    # --- STRICT SYSTEM PROMPT ---
    sys_prompt = f"""
    SYSTEM: You are Jarvis, a Functional AI Agent with REAL-WORLD ACCESS.
    
    YOUR CAPABILITIES:
    - You CAN send emails.
    - You CAN read emails.
    - You CAN search Google and YouTube.
    
    MEMORIES: {memories}
    CALENDAR: {calendar_data}
    DATE: {datetime.datetime.now().strftime("%d %B %Y")}
    
    INSTRUCTIONS:
    - Do NOT refuse to send emails. The user has authenticated you.
    - If the user asks to email, output the JSON command immediately.
    
    TOOLS (OUTPUT JSON ONLY):
    - Search -> {{"action": "search", "query": "..."}}
    - Videos -> {{"action": "search_video", "query": "..."}}
    - Email -> {{"action": "send_email", "to": "email@address.com", "subject": "Short Subject", "body": "Full body content"}}
    - Memory -> {{"action": "save_memory", "text": "..."}}
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
