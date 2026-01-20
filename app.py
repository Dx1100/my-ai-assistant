import streamlit as st
import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
import datetime
import io
import asyncio
import edge_tts
import json
import time
import pypdf 

# --- CONFIG ---
st.set_page_config(page_title="Jarvis Pro", page_icon="🧠", layout="wide")

# !!! REPLACE WITH YOUR EMAIL !!!
MY_EMAIL = "mybusiness110010@gmail.com" 
# !!! REPLACE WITH YOUR CALENDAR ID (Usually just your email) !!!
CALENDAR_ID = "mybusiness110010@gmail.com"

# --- 1. SETUP CREDENTIALS (SELF-HEALING) ---
drive_service = None
cal_service = None

try:
    key_dict = None
    
    # 1. Load the data from secrets
    if "FIREBASE_KEY" in st.secrets:
        # Check if it's the Nested TOML format
        if isinstance(st.secrets["FIREBASE_KEY"], dict):
            key_dict = dict(st.secrets["FIREBASE_KEY"])
        # Check if it's a JSON string
        elif isinstance(st.secrets["FIREBASE_KEY"], str):
             key_dict = json.loads(st.secrets["FIREBASE_KEY"])
    
    if key_dict:
        # --- THE FIX: REPAIR THE PRIVATE KEY FORMAT ---
        # This converts the text "\n" into actual Enter key presses
        if "private_key" in key_dict:
            key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")

        # Create Credentials
        creds = service_account.Credentials.from_service_account_info(
            key_dict, 
            scopes=['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/calendar']
        )
        drive_service = build('drive', 'v3', credentials=creds)
        cal_service = build('calendar', 'v3', credentials=creds)
    else:
        st.error("Credentials not found. Please check secrets.toml")
        
except Exception as e:
    st.error(f"Credential Error: {e}")

if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

try:
    model = genai.GenerativeModel('models/gemini-2.0-flash')
except:
    model = genai.GenerativeModel('models/gemini-1.5-flash')

# --- 2. DRIVE FUNCTIONS ---
def share_file_with_user(file_id):
    try:
        user_permission = {'type': 'user', 'role': 'writer', 'emailAddress': MY_EMAIL}
        drive_service.permissions().create(
            fileId=file_id, body=user_permission, fields='id'
        ).execute()
    except: pass

def get_file_content(filename):
    if not drive_service: return ""
    try:
        results = drive_service.files().list(
            q=f"name='{filename}' and trashed=false", fields="files(id, name)").execute()
        files = results.get('files', [])
        if not files: return "" 
        
        file_id = files[0]['id']
        share_file_with_user(file_id)
        
        request = drive_service.files().get_media(fileId=file_id)
        file_content = io.BytesIO()
        downloader = MediaIoBaseDownload(file_content, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
        return file_content.getvalue().decode('utf-8')
    except: return ""

def update_file(filename, new_content):
    if not drive_service: return False
    try:
        results = drive_service.files().list(
            q=f"name='{filename}' and trashed=false", fields="files(id, name)").execute()
        files = results.get('files', [])
        
        media = MediaIoBaseUpload(io.BytesIO(new_content.encode('utf-8')), mimetype='text/plain')
        
        if not files:
            file_metadata = {'name': filename}
            file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            share_file_with_user(file.get('id'))
        else:
            file_id = files[0]['id']
            drive_service.files().update(fileId=file_id, media_body=media).execute()
            share_file_with_user(file_id)
        return True
    except: return False

# --- 3. CALENDAR FUNCTIONS ---
def get_upcoming_events():
    if not cal_service: return "Calendar not connected."
    try:
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events_result = cal_service.events().list(
            calendarId=CALENDAR_ID, timeMin=now,
            maxResults=5, singleEvents=True,
            orderBy='startTime').execute()
        events = events_result.get('items', [])
        if not events: return "No upcoming events."
        
        event_list = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            event_list.append(f"- {start}: {event['summary']}")
        return "\n".join(event_list)
    except Exception as e:
        return f"Calendar Error: {e}"

def add_calendar_event(summary, start_time_str):
    if not cal_service: return False
    try:
        # Accepted format: "2025-01-20T15:00:00"
        start_dt = datetime.datetime.fromisoformat(start_time_str)
        end_dt = start_dt + datetime.timedelta(hours=1) 
        
        event = {
            'summary': summary,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Kolkata'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Asia/Kolkata'},
        }
        cal_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        return True
    except: return False

# --- 4. UTILS ---
async def text_to_speech(text):
    filename = f"reply_{int(time.time())}.mp3"
    communicate = edge_tts.Communicate(text, "en-US-AriaNeural")
    await communicate.save(filename)
    return filename

def process_pdf_upload(uploaded_file):
    try:
        pdf_reader = pypdf.PdfReader(uploaded_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
        return text
    except: return None

# --- 5. THE AGENT ---
def run_agent(user_input, input_type, chat_history):
    # Fetch Data
    memory = get_file_content("Jarvis_Memory.txt")
    tasks = get_file_content("Jarvis_Tasks.txt")
    calendar = get_upcoming_events()
    
    recent_chat = ""
    for msg in chat_history[-8:]:
        role = "USER" if msg["role"] == "user" else "JARVIS"
        content = msg["content"].replace("🎤 [Audio Message]", "[Audio Data]")
        recent_chat += f"{role}: {content}\n"

    today = datetime.datetime.now().strftime("%A, %Y-%m-%d %H:%M")

    sys_prompt = f"""
    SYSTEM: You are Jarvis.
    DATE/TIME: {today} (Asia/Kolkata)
    
    === LONG TERM MEMORY ===
    {memory[:15000] if memory else "Empty"}
    
    === UPCOMING CALENDAR ===
    {calendar}
    
    === CURRENT TASK LIST ===
    {tasks[:5000] if tasks else "Empty"}
    
    === RECENT CHAT ===
    {recent_chat}
    
    === INSTRUCTIONS ===
    1. Answer naturally.
    2. SCHEDULE: If user wants to schedule something, output JSON with action="schedule". Format time as ISO (YYYY-MM-DDTHH:MM:SS).
    3. MEMORY: If new facts, output JSON with action="update_memory".
    4. TASKS: If general to-dos (not specific time), output JSON with action="update_tasks".
    
    === OUTPUT FORMAT (JSON ONLY) ===
    {{ "action": "schedule", "summary": "Meeting with X", "time": "2025-10-20T14:00:00", "reply_to_user": "Scheduled..." }}
    OR
    {{ "action": "update_memory", "new_memory": "...", "reply_to_user": "..." }}
    OR
    {{ "action": "update_tasks", "new_tasks": "...", "reply_to_user": "..." }}
    OR
    {{ "action": "none", "reply_to_user": "..." }}
    """
    
    try:
        if input_type == "audio":
            response = model.generate_content([sys_prompt, "USER (Audio):", {"mime_type": "audio/wav", "data": user_input}])
        else:
            response = model.generate_content([sys_prompt, f"USER: {user_input}"])
        return response.text
    except Exception as e:
        if "429" in str(e):
             time.sleep(2)
             return run_agent(user_input, input_type, chat_history)
        return f"AI Error: {e}"

# --- 6. UI LAYOUT ---
st.title("🧠 Jarvis Pro")

with st.sidebar:
    st.header("📚 Knowledge")
    uploaded_file = st.file_uploader("Upload PDF", type=["pdf"])
    if uploaded_file and st.button("Memorize"):
        with st.spinner("Reading..."):
            raw_text = process_pdf_upload(uploaded_file)
            if raw_text:
                prompt = f"Summarize and add to memory:\n{raw_text[:20000]}"
                reply = run_agent(prompt, "text", [])
                if "{" in reply:
                     try:
                         data = json.loads(reply[reply.find("{"):reply.rfind("}")+1])
                         if "new_memory" in data:
                             update_file("Jarvis_Memory.txt", data["new_memory"])
                             st.success("Memorized!")
                     except: pass

    st.divider()
    if st.button("🔄 Refresh Data"): st.rerun()
    
    if drive_service:
        mem = get_file_content("Jarvis_Memory.txt")
        cal = get_upcoming_events()
        with st.expander("Memory", expanded=False): st.write(mem)
        with st.expander("Calendar", expanded=True): st.write(cal)

# Chat UI
if "chat_history" not in st.session_state: st.session_state.chat_history = []
if "last_processed_audio" not in st.session_state: st.session_state.last_processed_audio = None

for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])
        if "audio_file" in msg: st.audio(msg["audio_file"], autoplay=False)

st.divider()
col1, col2 = st.columns([4, 1])
with col1: text_input = st.chat_input("Type a message...")
with col2: audio_val = st.audio_input("🎤")

# Logic
final_input = None
input_type = "text"
should_run = False

if audio_val is not None:
    if audio_val != st.session_state.last_processed_audio:
        final_input = audio_val.read()
        input_type = "audio"
        should_run = True
        st.session_state.last_processed_audio = audio_val

if text_input and not should_run:
    final_input = text_input
    input_type = "text"
    should_run = True

if should_run:
    if input_type == "text":
        st.session_state.chat_history.append({"role": "user", "content": final_input})
        with st.chat_message("user"): st.write(final_input)
    else:
        st.session_state.chat_history.append({"role": "user", "content": "🎤 [Audio]"})
        with st.chat_message("user"): st.write("🎤 [Audio]")

    with st.spinner("Processing..."):
        reply = run_agent(final_input, input_type, st.session_state.chat_history)
        display_text = reply 
        
        # JSON Parsing
        if "{" in reply and "reply_to_user" in reply:
            try:
                json_str = reply[reply.find("{"):reply.rfind("}")+1]
                data = json.loads(json_str)
                if "reply_to_user" in data: display_text = data["reply_to_user"]
                
                # Execute Actions
                if data.get("action") == "update_memory":
                    update_file("Jarvis_Memory.txt", data["new_memory"])
                    st.toast("Memory Updated", icon="💾")
                elif data.get("action") == "update_tasks":
                    update_file("Jarvis_Tasks.txt", data["new_tasks"])
                    st.toast("Tasks Updated", icon="✅")
                elif data.get("action") == "schedule":
                    success = add_calendar_event(data["summary"], data["time"])
                    if success: st.toast("Event Scheduled!", icon="📅")
                    else: st.error("Schedule Failed. Check Permissions.")
            except: pass

        try:
            clean_text = display_text.replace("*", "").replace("#", "")
            audio_path = asyncio.run(text_to_speech(clean_text))
        except: audio_path = None

        st.session_state.chat_history.append({"role": "assistant", "content": display_text, "audio_file": audio_path})
        with st.chat_message("assistant"):
            st.write(display_text)
            if audio_path: st.audio(audio_path, autoplay=True)
