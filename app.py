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

# --- CONFIG ---
st.set_page_config(page_title="Jarvis Mobile", page_icon="🧠", layout="centered")

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
            scopes=['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/calendar']
        )
        drive_service = build('drive', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"Credential Error: {e}")

if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

model = genai.GenerativeModel('models/gemini-1.5-flash')

# --- 2. DRIVE FUNCTIONS ---
def get_file_content(filename):
    if not drive_service: return ""
    try:
        results = drive_service.files().list(
            q=f"name='{filename}' and trashed=false", fields="files(id, name)").execute()
        files = results.get('files', [])
        if not files: return "" # File doesn't exist yet
        
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
    communicate = edge_tts.Communicate(text, "en-US-AriaNeural")
    out_file = "reply.mp3"
    await communicate.save(out_file)
    return out_file

# --- 4. THE AGENT (HANDLES TEXT & AUDIO) ---
def run_agent(user_input, input_type="text"):
    # Read Brain
    memory = get_file_content("Jarvis_Memory.txt")
    tasks = get_file_content("Jarvis_Tasks.txt")
    today = datetime.datetime.now().strftime("%A, %Y-%m-%d")

    # The Prompt
    sys_prompt = f"""
    SYSTEM: You are Jarvis.
    DATE: {today}
    
    MY MEMORY: {memory if memory else "No memory yet."}
    MY TASKS: {tasks if tasks else "No tasks yet."}
    
    INSTRUCTION:
    1. Reply to the user.
    2. If user teaches you something, output JSON to update Memory.
    3. If user changes plans, output JSON to update Tasks.
    
    OUTPUT FORMAT:
    {{ "action": "update", "new_memory": "...", "new_tasks": "...", "reply_to_user": "..." }}
    OR just plain text if no update needed.
    """
    
    try:
        if input_type == "audio":
            # Audio Input
            response = model.generate_content([sys_prompt, {"mime_type": "audio/wav", "data": user_input}])
        else:
            # Text Input
            response = model.generate_content([sys_prompt, f"USER: {user_input}"])
            
        return response.text
    except Exception as e:
        return f"Error: {e}"

# --- 5. UI LAYOUT ---
st.title("🎙️ Jarvis Mobile")

# A. Container for Chat History (Optional visual)
with st.container():
    if "last_reply" in st.session_state:
        st.chat_message("assistant").write(st.session_state.last_reply)

# B. INPUTS
# 1. Audio Widget
audio_val = st.audio_input("Voice Command")
# 2. Text Input (Pinned to bottom)
text_val = st.chat_input("Type a message...")

final_input = None
input_type = "text"

# Check which input was used
if audio_val:
    final_input = audio_val.read()
    input_type = "audio"
elif text_val:
    final_input = text_val
    input_type = "text"

# C. PROCESS INPUT
if final_input:
    with st.spinner("Thinking..."):
        reply = run_agent(final_input, input_type)
        
        display_text = reply
        
        # Check for JSON updates
        if "{" in reply and "action" in reply:
            try:
                json_str = reply[reply.find("{"):reply.rfind("}")+1]
                data = json.loads(json_str)
                
                if data.get("action") == "update":
                    if "new_memory" in data:
                        update_file("Jarvis_Memory.txt", data["new_memory"])
                        st.toast("Brain Updated!", icon="🧠")
                    if "new_tasks" in data:
                        update_file("Jarvis_Tasks.txt", data["new_tasks"])
                        st.toast("Tasks Updated!", icon="✅")
                    display_text = data.get("reply_to_user", "Done.")
            except:
                pass

        # Save to session state to keep it on screen
        st.session_state.last_reply = display_text
        st.chat_message("assistant").write(display_text)

        # Speak it out (Only if audio was used OR user wants it)
        # We auto-play audio regardless of input type for that "Jarvis" feel
        try:
            audio_file = asyncio.run(text_to_speech(display_text.replace("*", "")))
            st.audio(audio_file, autoplay=True)
        except:
            pass

# D. BRAIN VIEW
with st.expander("View My Brain (Drive Data)"):
    mem = get_file_content("Jarvis_Memory.txt")
    tsk = get_file_content("Jarvis_Tasks.txt")
    
    if not mem and not tsk:
        st.info("Brain is empty. Talk to me to create memories!")
    else:
        st.text_area("Memory", mem, height=100)
        st.text_area("Tasks", tsk, height=100)
