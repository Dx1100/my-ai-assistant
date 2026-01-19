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

# --- CONFIG ---
st.set_page_config(page_title="Jarvis Pro", page_icon="🧠", layout="centered")

# --- 1. SETUP CREDENTIALS ---
drive_service = None

if "FIREBASE_KEY" in st.secrets:
    try:
        secret_data = st.secrets["FIREBASE_KEY"]
        if isinstance(secret_data, str):
            key_dict = json.loads(secret_data)
        else:
            key_dict = dict(secret_data)
            
        creds = service_account.Credentials.from_service_account_info(
            key_dict, 
            scopes=['https://www.googleapis.com/auth/drive']
        )
        drive_service = build('drive', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"Credential Error: {e}")

if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

# Model Setup
try:
    model = genai.GenerativeModel('models/gemini-2.0-flash')
except:
    model = genai.GenerativeModel('models/gemini-1.5-flash')

# --- 2. DRIVE FUNCTIONS ---
def get_file_content(filename):
    if not drive_service: return ""
    try:
        # Prevent caching by adding a unique query param if needed, but here we just re-fetch
        results = drive_service.files().list(
            q=f"name='{filename}' and trashed=false", fields="files(id, name)").execute()
        files = results.get('files', [])
        if not files: return "" 
        
        file_id = files[0]['id']
        request = drive_service.files().get_media(fileId=file_id)
        file_content = io.BytesIO()
        downloader = MediaIoBaseDownload(file_content, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
        return file_content.getvalue().decode('utf-8')
    except:
        return ""

def update_file(filename, new_content):
    if not drive_service: return False
    try:
        results = drive_service.files().list(
            q=f"name='{filename}' and trashed=false", fields="files(id, name)").execute()
        files = results.get('files', [])
        
        media = MediaIoBaseUpload(io.BytesIO(new_content.encode('utf-8')), mimetype='text/plain')
        
        if not files:
            file_metadata = {'name': filename}
            drive_service.files().create(body=file_metadata, media_body=media).execute()
        else:
            file_id = files[0]['id']
            drive_service.files().update(fileId=file_id, media_body=media).execute()
        return True
    except:
        return False

# --- 3. SPEECH OUTPUT ---
async def text_to_speech(text):
    filename = f"reply_{int(time.time())}.mp3"
    communicate = edge_tts.Communicate(text, "en-US-AriaNeural")
    await communicate.save(filename)
    return filename

# --- 4. THE AGENT (WITH RETRY LOGIC) ---
def run_agent(user_input, input_type, chat_history):
    # 1. Fetch Long Term Memory
    memory = get_file_content("Jarvis_Memory.txt")
    tasks = get_file_content("Jarvis_Tasks.txt")
    
    # 2. Format Context
    recent_chat = ""
    for msg in chat_history[-15:]:
        role = "USER" if msg["role"] == "user" else "JARVIS"
        content = msg["content"].replace("🎤 [Audio Message]", "[Audio Data]")
        recent_chat += f"{role}: {content}\n"

    today = datetime.datetime.now().strftime("%A, %Y-%m-%d")

    # 3. Prompt
    sys_prompt = f"""
    SYSTEM: You are Jarvis.
    DATE: {today}
    
    === LONG TERM MEMORY ===
    {memory if memory else "No memory yet."}
    
    === CURRENT TASKS ===
    {tasks if tasks else "No tasks yet."}
    
    === RECENT CHAT ===
    {recent_chat}
    
    INSTRUCTIONS:
    1. Answer naturally.
    2. UPDATE MEMORY: Output JSON if user teaches facts.
    3. UPDATE TASKS: Output JSON if user changes plans.
    4. NO UPDATE: Output JSON with action="none".
    
    OUTPUT FORMAT (JSON):
    {{ "action": "update", "new_memory": "...", "new_tasks": "...", "reply_to_user": "..." }}
    OR
    {{ "action": "none", "reply_to_user": "..." }}
    """
    
    # --- RETRY LOOP (FIX FOR 429 ERROR) ---
    max_retries = 3
    for attempt in range(max_retries):
        try:
            if input_type == "audio":
                response = model.generate_content([sys_prompt, "USER (Audio):", {"mime_type": "audio/wav", "data": user_input}])
            else:
                response = model.generate_content([sys_prompt, f"USER: {user_input}"])
            return response.text
            
        except Exception as e:
            error_str = str(e)
            if "429" in error_str:
                # If we hit the speed limit, wait and try again
                time.sleep(2 * (attempt + 1)) # Wait 2s, then 4s, then 6s
                continue
            else:
                return f"AI Error: {e}"
    
    return "I am overloaded right now. Please wait 10 seconds."

# --- 5. UI LAYOUT ---
st.title("🧠 Jarvis Pro")

# --- SESSION STATE ---
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "last_processed_audio" not in st.session_state:
    st.session_state.last_processed_audio = None

# --- DISPLAY HISTORY ---
for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])
        if "audio_file" in msg:
            st.audio(msg["audio_file"], autoplay=False)

st.divider()

# --- INPUT AREA ---
col1, col2 = st.columns([4, 1])
with col1:
    text_input = st.chat_input("Type a message...")
with col2:
    audio_val = st.audio_input("🎤")

# --- LOGIC ---
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

# --- EXECUTION ---
if should_run:
    # 1. UI: Show User Input
    if input_type == "text":
        st.session_state.chat_history.append({"role": "user", "content": final_input})
        with st.chat_message("user"):
            st.write(final_input)
    else:
        st.session_state.chat_history.append({"role": "user", "content": "🎤 [Audio Message]"})
        with st.chat_message("user"):
            st.write("🎤 [Audio Message]")

    # 2. Logic: Run AI with History
    with st.spinner("Processing..."):
        # Pass the history to the agent!
        reply = run_agent(final_input, input_type, st.session_state.chat_history)
        display_text = reply 
        
        # 3. Parse JSON Response
        if "{" in reply and "reply_to_user" in reply:
            try:
                json_str = reply[reply.find("{"):reply.rfind("}")+1]
                data = json.loads(json_str)
                
                if "reply_to_user" in data:
                    display_text = data["reply_to_user"]
                
                if data.get("action") == "update":
                    if "new_memory" in data:
                        update_file("Jarvis_Memory.txt", data["new_memory"])
                        st.toast("Memory File Updated", icon="💾")
                    if "new_tasks" in data:
                        update_file("Jarvis_Tasks.txt", data["new_tasks"])
                        st.toast("Tasks File Updated", icon="✅")
            except:
                pass

        # 4. Generate Audio
        try:
            clean_text = display_text.replace("*", "").replace("#", "")
            audio_path = asyncio.run(text_to_speech(clean_text))
        except:
            audio_path = None

        # 5. UI: Show Assistant Response
        msg_data = {"role": "assistant", "content": display_text}
        if audio_path:
            msg_data["audio_file"] = audio_path
        
        st.session_state.chat_history.append(msg_data)
        
        with st.chat_message("assistant"):
            st.write(display_text)
            if audio_path:
                st.audio(audio_path, autoplay=True)

# --- BRAIN VIEW (Improved) ---
with st.expander("🧠 View Live Memory (Click Refresh to Check)"):
    if st.button("🔄 Refresh Brain Data"):
        # This button forces the UI to redraw, calling get_file_content again
        st.rerun()

    if drive_service:
        mem_content = get_file_content("Jarvis_Memory.txt")
        task_content = get_file_content("Jarvis_Tasks.txt")
        
        st.caption("This data is stored in your Google Drive.")
        st.text_area("Long Term Memory", mem_content, height=150)
        st.text_area("Current Tasks", task_content, height=150)
        
