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
# (We assume you set up secrets.toml as discussed before)
if "FIREBASE_KEY" in st.secrets:
    key_dict = json.loads(st.secrets["FIREBASE_KEY"])
    creds = service_account.Credentials.from_service_account_info(
        key_dict, scopes=['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/calendar']
    )
    drive_service = build('drive', 'v3', credentials=creds)
    cal_service = build('calendar', 'v3', credentials=creds)

if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

# Use Flash for speed and long context
model = genai.GenerativeModel('models/gemini-1.5-flash')

# --- 2. DRIVE MEMORY FUNCTIONS (THE BRAIN) ---
def get_file_content(filename):
    """Reads your long-term memory from Drive"""
    try:
        # Search for file
        results = drive_service.files().list(
            q=f"name='{filename}' and trashed=false", fields="files(id, name)").execute()
        files = results.get('files', [])
        
        if not files: return "" # File doesn't exist yet
        
        # Download content
        file_id = files[0]['id']
        request = drive_service.files().get_media(fileId=file_id)
        file_content = io.BytesIO()
        downloader = MediaIoBaseDownload(file_content, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
        return file_content.getvalue().decode('utf-8')
    except Exception as e:
        return f"Error reading memory: {e}"

def update_file(filename, new_content):
    """Overwrites the file with updated memory/tasks"""
    try:
        # Search for file to get ID
        results = drive_service.files().list(
            q=f"name='{filename}' and trashed=false", fields="files(id, name)").execute()
        files = results.get('files', [])
        
        media = MediaIoBaseUpload(io.BytesIO(new_content.encode('utf-8')), mimetype='text/plain')
        
        if not files:
            # Create new
            file_metadata = {'name': filename}
            drive_service.files().create(body=file_metadata, media_body=media).execute()
        else:
            # Update existing
            file_id = files[0]['id']
            drive_service.files().update(fileId=file_id, media_body=media).execute()
        return True
    except:
        return False

# --- 3. SPEECH OUTPUT ---
async def text_to_speech(text):
    communicate = edge_tts.Communicate(text, "en-US-AriaNeural") # Fast, clear voice
    out_file = "reply.mp3"
    await communicate.save(out_file)
    return out_file

# --- 4. THE AI AGENT ---
def run_agent(audio_bytes):
    # 1. READ CONTEXT
    memory = get_file_content("Jarvis_Memory.txt")
    tasks = get_file_content("Jarvis_Tasks.txt")
    today = datetime.datetime.now().strftime("%A, %Y-%m-%d")

    # 2. CONSTRUCT PROMPT (The "Long Context" Magic)
    # We feed the WHOLE memory file into Gemini. 
    # Gemini 1.5 Flash has a 1 Million token window, so this can get HUGE without issues.
    prompt = f"""
    SYSTEM: You are Jarvis, a mobile personal assistant.
    DATE: {today}
    
    MY LONG TERM MEMORY (What I have learned):
    {memory}
    
    MY CURRENT TASKS/PLAN:
    {tasks}
    
    INSTRUCTION:
    1. Listen to the user audio.
    2. Respond helpfully.
    3. IMPORTANT: If the user taught you something new, output a JSON to update 'Memory'.
    4. IMPORTANT: If the user changed plans/tasks, output a JSON to update 'Tasks'.
    
    OUTPUT FORMAT (JSON ONLY for updates, Plain Text for talk):
    If updating files:
    {{ "action": "update", "new_memory": "...", "new_tasks": "...", "reply_to_user": "..." }}
    
    If just talking:
    Just type the response text.
    """

    # 3. CALL GEMINI (Audio + Text directly)
    response = model.generate_content([prompt, {"mime_type": "audio/wav", "data": audio_bytes}])
    return response.text

# --- 5. UI LAYOUT ---
st.title("🎙️ Jarvis Mobile")

# Audio Input (Native Mobile Widget)
audio_value = st.audio_input("Tap to Speak")

if audio_value:
    with st.spinner("Thinking..."):
        # Convert audio to bytes for Gemini
        audio_bytes = audio_value.read()
        
        # Run the Brain
        reply = run_agent(audio_bytes)
        
        final_speech = reply
        
        # Check if we need to save data
        if "{" in reply and "action" in reply:
            try:
                # cleaner parsing
                json_str = reply[reply.find("{"):reply.rfind("}")+1]
                data = json.loads(json_str)
                
                if data.get("action") == "update":
                    if "new_memory" in data:
                        update_file("Jarvis_Memory.txt", data["new_memory"])
                        st.toast("Memory Saved!", icon="💾")
                    if "new_tasks" in data:
                        update_file("Jarvis_Tasks.txt", data["new_tasks"])
                        st.toast("Tasks Updated!", icon="✅")
                    final_speech = data["reply_to_user"]
            except:
                pass # Fallback to raw text if JSON fails
        
        # Display & Speak
        st.chat_message("assistant").write(final_speech)
        
        # Generate Audio Reply
        audio_file = asyncio.run(text_to_speech(final_speech))
        st.audio(audio_file, autoplay=True)

# Show current context (Optional, for you to see)
with st.expander("View My Brain (Drive Data)"):
    st.text_area("Memory", get_file_content("Jarvis_Memory.txt"), height=150)
    st.text_area("Tasks", get_file_content("Jarvis_Tasks.txt"), height=150)
