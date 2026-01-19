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
st.set_page_config(page_title="Jarvis Pro", page_icon="🧠", layout="centered")

# !!! IMPORTANT: PUT YOUR EMAIL HERE !!!
# This allows the robot to share the memory files with you so you can see them.
MY_EMAIL = "mybusiness110010@gmail.com" 

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

# --- 2. DRIVE FUNCTIONS (FIXED SHARING) ---
def share_file_with_user(file_id):
    """Shares the file with your personal email so you can see it"""
    try:
        user_permission = {
            'type': 'user',
            'role': 'writer',
            'emailAddress': mybusiness110010@gmail.com
        }
        drive_service.permissions().create(
            fileId=file_id,
            body=user_permission,
            fields='id',
        ).execute()
    except Exception as e:
        # If already shared, it might error, which is fine
        pass

def get_file_content(filename):
    if not drive_service: return ""
    try:
        results = drive_service.files().list(
            q=f"name='{filename}' and trashed=false", fields="files(id, name)").execute()
        files = results.get('files', [])
        if not files: return "" 
        
        file_id = files[0]['id']
        
        # --- AUTO FIX: Share it if found but not visible to you ---
        share_file_with_user(file_id)
        
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
            # Create NEW file
            file_metadata = {'name': filename}
            file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            # SHARE IT IMMEDIATELY
            share_file_with_user(file.get('id'))
        else:
            # Update EXISTING
            file_id = files[0]['id']
            drive_service.files().update(fileId=file_id, media_body=media).execute()
            # Ensure it is shared
            share_file_with_user(file_id)
        return True
    except:
        return False

# --- 3. SPEECH OUTPUT ---
async def text_to_speech(text):
    filename = f"reply_{int(time.time())}.mp3"
    communicate = edge_tts.Communicate(text, "en-US-AriaNeural")
    await communicate.save(filename)
    return filename

# --- 4. PDF PROCESSING ---
def process_pdf_upload(uploaded_file):
    try:
        pdf_reader = pypdf.PdfReader(uploaded_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
        return text
    except Exception as e:
        return None

# --- 5. THE AGENT ---
def run_agent(user_input, input_type, chat_history):
    memory = get_file_content("Jarvis_Memory.txt")
    tasks = get_file_content("Jarvis_Tasks.txt")
    
    recent_chat = ""
    for msg in chat_history[-10:]:
        role = "USER" if msg["role"] == "user" else "JARVIS"
        content = msg["content"].replace("🎤 [Audio Message]", "[Audio Data]")
        recent_chat += f"{role}: {content}\n"

    today = datetime.datetime.now().strftime("%A, %Y-%m-%d")

    sys_prompt = f"""
    SYSTEM: You are Jarvis.
    DATE: {today}
    
    === LONG TERM MEMORY ===
    {memory if memory else "No memory yet."}
    
    === CURRENT TASKS ===
    {tasks if tasks else "No tasks yet."}
    
    === RECENT CONVERSATION ===
    {recent_chat}
    
    === INSTRUCTIONS ===
    1. Answer naturally.
    2. UPDATE MEMORY: Output JSON to update 'Memory' with new facts.
    3. UPDATE TASKS: Output JSON to update 'Tasks' with plans.
    4. NO UPDATE: Output JSON with action="none".
    
    === OUTPUT FORMAT (JSON ONLY) ===
    {{ "action": "update", "new_memory": "...", "new_tasks": "...", "reply_to_user": "..." }}
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

# --- SIDEBAR: KNOWLEDGE UPLOAD ---
with st.sidebar:
    st.header("📚 Add Knowledge")
    uploaded_file = st.file_uploader("Upload PDF to Memory", type=["pdf"])
    
    if uploaded_file and st.button("Process & Memorize"):
        with st.spinner("Reading Document..."):
            raw_text = process_pdf_upload(uploaded_file)
            if raw_text:
                st.info("Extracting key facts...")
                upload_prompt = f"Summarize key knowledge from this text and add to Long Term Memory. \n\nDOC:\n{raw_text[:30000]}"
                reply = run_agent(upload_prompt, "text", st.session_state.get("chat_history", []))
                
                if "{" in reply and "new_memory" in reply:
                     json_str = reply[reply.find("{"):reply.rfind("}")+1]
                     data = json.loads(json_str)
                     update_file("Jarvis_Memory.txt", data["new_memory"])
                     st.success("Knowledge Added!")
            else:
                st.error("Could not read PDF.")

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

if should_run:
    if input_type == "text":
        st.session_state.chat_history.append({"role": "user", "content": final_input})
        with st.chat_message("user"):
            st.write(final_input)
    else:
        st.session_state.chat_history.append({"role": "user", "content": "🎤 [Audio Message]"})
        with st.chat_message("user"):
            st.write("🎤 [Audio Message]")

    with st.spinner("Processing..."):
        reply = run_agent(final_input, input_type, st.session_state.chat_history)
        display_text = reply 
        
        if "{" in reply and "reply_to_user" in reply:
            try:
                json_str = reply[reply.find("{"):reply.rfind("}")+1]
                data = json.loads(json_str)
                if "reply_to_user" in data: display_text = data["reply_to_user"]
                
                if data.get("action") == "update":
                    if "new_memory" in data:
                        update_file("Jarvis_Memory.txt", data["new_memory"])
                        st.toast("Memory Updated", icon="💾")
                    if "new_tasks" in data:
                        update_file("Jarvis_Tasks.txt", data["new_tasks"])
                        st.toast("Tasks Updated", icon="✅")
            except: pass

        try:
            clean_text = display_text.replace("*", "").replace("#", "")
            audio_path = asyncio.run(text_to_speech(clean_text))
        except: audio_path = None

        st.session_state.chat_history.append({"role": "assistant", "content": display_text, "audio_file": audio_path})
        
        with st.chat_message("assistant"):
            st.write(display_text)
            if audio_path: st.audio(audio_path, autoplay=True)

with st.expander("🧠 View Live Memory"):
    if st.button("🔄 Refresh"): st.rerun()
    if drive_service:
        st.text_area("Long Term Memory", get_file_content("Jarvis_Memory.txt"), height=150)
        st.text_area("Current Tasks", get_file_content("Jarvis_Tasks.txt"), height=150)
