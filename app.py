import streamlit as st
import google.generativeai as genai
import firebase_admin
from firebase_admin import credentials, firestore
import edge_tts
import asyncio
import json
import tempfile
import smtplib
import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
import datetime
from PIL import Image
import PyPDF2

# --- CONFIGURATION ---
st.set_page_config(page_title="Jarvis Pro", layout="wide", page_icon="💼")

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

# 2. Setup Google Services (Drive & Calendar)
SCOPES = ['https://www.googleapis.com/auth/calendar', 'https://www.googleapis.com/auth/drive']
creds = None
drive_service = None
cal_service = None

if "FIREBASE_KEY" in st.secrets:
    try:
        key_dict = json.loads(st.secrets["FIREBASE_KEY"]) if isinstance(st.secrets["FIREBASE_KEY"], str) else dict(st.secrets["FIREBASE_KEY"])
        creds = service_account.Credentials.from_service_account_info(key_dict, scopes=SCOPES)
        drive_service = build('drive', 'v3', credentials=creds)
        cal_service = build('calendar', 'v3', credentials=creds)
    except Exception as e: st.error(f"Google Services Error: {e}")

# 3. Setup Brain
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
model = genai.GenerativeModel('models/gemini-2.0-flash')

# --- RESEARCH TOOLS ---

def google_search(query):
    """General Web Search"""
    if "GOOGLE_SEARCH_KEY" not in st.secrets: return "Error: No Search Keys"
    try:
        service = build("customsearch", "v1", developerKey=st.secrets["GOOGLE_SEARCH_KEY"])
        result = service.cse().list(q=query, cx=st.secrets["GOOGLE_SEARCH_CX"], num=5).execute()
        items = result.get('items', [])
        if not items: return "No results."
        
        # We format it so the AI clearly sees the link to cite
        formatted_results = ""
        for i in items:
            formatted_results += f"Source: {i['title']}\nURL: {i['link']}\nSnippet: {i['snippet']}\n\n"
        return formatted_results
    except Exception as e: return f"Search Failed: {e}"

def search_youtube(query):
    """Specific YouTube Search"""
    if "GOOGLE_SEARCH_KEY" not in st.secrets: return "Error: No Search Keys"
    try:
        # We append 'site:youtube.com' to force video results
        youtube_query = f"{query} site:youtube.com"
        service = build("customsearch", "v1", developerKey=st.secrets["GOOGLE_SEARCH_KEY"])
        result = service.cse().list(q=youtube_query, cx=st.secrets["GOOGLE_SEARCH_CX"], num=3).execute()
        items = result.get('items', [])
        if not items: return "No videos found."
        
        video_data = ""
        for i in items:
            video_data += f"Video Title: {i['title']}\nVideo Link: {i['link']}\n\n"
        return video_data
    except Exception as e: return f"Video Search Failed: {e}"

# --- EMAIL TOOLS ---
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

def read_emails(limit=3):
    if "GMAIL_USER" not in st.secrets: return "Error: Gmail secrets missing."
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(st.secrets["GMAIL_USER"], st.secrets["GMAIL_PASSWORD"])
        mail.select("inbox")
        status, messages = mail.search(None, '(UNSEEN)')
        email_ids = messages[0].split()[-limit:]
        
        result = []
        for e_id in email_ids:
            _, msg_data = mail.fetch(e_id, "(RFC822)")
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    result.append(f"📩 From: {msg['from']} | Sub: {msg['subject']}")
        mail.close()
        mail.logout()
        return "\n".join(result) if result else "No new unread emails."
    except Exception as e: return f"Read Email Error: {e}"

# --- DRIVE TOOLS ---
def save_to_drive(filename, content):
    if not drive_service: return "Drive not connected."
    try:
        folder_id = None
        results = drive_service.files().list(q="name='Jarvis_Memory' and mimeType='application/vnd.google-apps.folder'", fields="files(id, name)").execute()
        items = results.get('files', [])
        if items: folder_id = items[0]['id']
        
        file_metadata = {'name': filename}
        if folder_id: file_metadata['parents'] = [folder_id]
        
        media = MediaIoBaseUpload(io.BytesIO(content.encode('utf-8')), mimetype='text/plain')
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return f"✅ Saved '{filename}' to Drive (ID: {file.get('id')})"
    except Exception as e: return f"Drive Save Error: {e}"

# --- CORE LOGIC ---
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

# --- UI ---
st.title("💼 Jarvis Pro (Productivity Mode)")

with st.sidebar:
    st.header("🧠 Memory & Files")
    if st.button("Check Unread Emails"):
        with st.spinner("Checking Inbox..."):
            st.info(read_emails())
    if db:
        with st.expander("Long-Term Memories"):
            for m in get_memories(): st.text(f"• {m}")

if "messages" not in st.session_state: st.session_state.messages = []

# Voice Input
audio_value = st.audio_input("🎙️ Command")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]): st.write(msg["content"])

user_text = st.chat_input("Type command...")
final_input = None

if audio_value:
    with st.spinner("Listening..."):
        final_input = transcribe_audio(audio_value)
elif user_text:
    final_input = user_text

if final_input:
    st.session_state.messages.append({"role": "user", "content": final_input})
    with st.chat_message("user"): st.write(final_input)

    memories = get_memories()
    
    # SYSTEM PROMPT UPDATED FOR CITATIONS & YOUTUBE
    sys_prompt = f"""
    SYSTEM: You are Jarvis.
    MEMORIES: {memories}
    DATE: {datetime.datetime.now().strftime("%d %B %Y")}
    
    CRITICAL INSTRUCTION FOR RESEARCH:
    1. When answering from SEARCH RESULTS, you MUST include the source link at the end of the sentence or paragraph.
       Format: "Fact goes here [Source Name](URL)."
    2. If the user asks for VIDEOS, output the YouTube Links clearly.
    
    TOOLS & COMMANDS:
    1. Search Google -> {{"action": "search", "query": "..."}}
    2. Find Videos -> {{"action": "search_video", "query": "..."}}
    3. Send Email -> {{"action": "email", "to": "...", "subject": "...", "body": "..."}}
    4. Save to Drive -> {{"action": "drive_save", "filename": "...", "content": "..."}}
    5. Save Memory -> {{"action": "save_memory", "text": "..."}}
    
    INSTRUCTION: output ONLY the JSON for actions.
    """
    
    reply = ask_gemini([sys_prompt, f"USER: {final_input}"])
    
    final_response = reply
    if "{" in reply and "action" in reply:
        try:
            data = json.loads(reply.replace("```json", "").replace("```", "").strip())
            
            if data["action"] == "search":
                # Research Mode
                with st.status(f"🔎 Researching: {data['query']}...", expanded=True) as status:
                    res = google_search(data["query"])
                    status.write("Found sources...")
                    # Feed results back to Brain
                    research_prompt = f"{sys_prompt}\nSEARCH DATA:\n{res}\nUSER REQUEST: {final_input}\nINSTRUCTION: Summarize and CITE sources."
                    final_response = ask_gemini(research_prompt)
                    status.update(label="✅ Done!", state="complete", expanded=False)

            elif data["action"] == "search_video":
                # Video Mode
                with st.status(f"🎥 Finding Videos: {data['query']}...", expanded=True) as status:
                    res = search_youtube(data["query"])
                    status.write("Found videos...")
                    final_response = f"Here are the videos I found:\n\n{res}"
                    status.update(label="✅ Found videos!", state="complete", expanded=False)

            elif data["action"] == "email":
                final_response = send_email(data["to"], data["subject"], data["body"])
            elif data["action"] == "drive_save":
                final_response = save_to_drive(data["filename"], data["content"])
            elif data["action"] == "save_memory":
                add_memory(data["text"])
                final_response = "🧠 Memory Saved."
                
        except Exception as e:
            final_response = f"Action Error: {e}"

    with st.chat_message("assistant"):
        st.write(final_response)
        if len(final_response) < 300:
             try:
                audio = asyncio.run(speak(final_response.replace("*", "").replace("http", "")))
                st.audio(audio, autoplay=True)
             except: pass
    
    st.session_state.messages.append({"role": "assistant", "content": final_response})
