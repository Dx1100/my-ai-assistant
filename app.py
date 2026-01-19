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

# --- 1. SETUP CREDENTIALS (FIXED) ---
drive_service = None
cal_service = None

if "FIREBASE_KEY" in st.secrets:
    try:
        # FIX: Check if Streamlit already converted it to a dict
        secret_data = st.secrets["FIREBASE_KEY"]
        if isinstance(secret_data, str):
            key_dict = json.loads(secret_data)  # It was a string, so parse it
        else:
            key_dict = dict(secret_data)        # It was already a dict, just use it
            
        # Create Credentials
        creds = service_account.Credentials.from_service_account_info(
            key_dict, 
            scopes=['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/calendar']
        )
        drive_service = build('drive', 'v3', credentials=creds)
        cal_service = build('calendar', 'v3', credentials=creds)
        st.toast("Cloud Connected Successfully", icon="☁️")
    except Exception as e:
        st.error(f"Credential Error: {e}")

if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

# Use Flash for speed and long context
model = genai.GenerativeModel('models/gemini-1.5-flash')

# --- 2. DRIVE MEMORY FUNCTIONS ---
def get_file_content(filename):
    """Reads your long-term memory from Drive"""
    if not drive_service: return "Error: Drive not connected."
    try:
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
    except Exception as e:
        return f"Error reading memory: {e}"

def update_file(filename, new_content):
    """Overwrites the file with updated memory/tasks"""
    if not drive_service: return False
    try:
        results = drive_service.files().list(
            q=f"name='{filename}' and trashed=false", fields="files(id, name)").execute()
        files = results.get('files', [])
        
        media = MediaIoBaseUpload(io.BytesIO(new_content.encode('utf-8')), mimetype='text/plain')
        
        if not files:
            file_metadata = {'name': filename, 'parents': []} 
            # Note: You can add specific folder ID in 'parents' if you want
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

# --- 4. THE AI AGENT ---
def run_agent(audio_bytes):
    memory = get_file_content("Jarvis_Memory.txt")
    tasks = get_file_content("Jarvis_Tasks.txt")
    today = datetime.datetime.now().strftime("%A, %Y-%m-%d")

    prompt = f"""
    SYSTEM: You are Jarvis, a mobile personal assistant.
    DATE: {today}
    
    MY LONG TERM MEMORY:
    {memory}
    
    MY CURRENT TASKS:
    {tasks}
    
    INSTRUCTION:
    1. Listen to the user audio.
    2. Respond helpfully.
    3. If the user taught you something new, output JSON to update 'Memory'.
    4. If the user changed plans/tasks, output JSON to update 'Tasks'.
    
    OUTPUT FORMAT (JSON ONLY for updates, Plain Text for talk):
    {{ "action": "update", "new_memory": "...", "new_tasks": "...", "reply_to_user": "..." }}
    """
    
    try:
        response = model.generate_content([prompt, {"mime_type": "audio/wav", "data": audio_bytes}])
        return response.text
    except Exception as e:
        return f"AI Error: {e}"

# --- 5. UI LAYOUT ---
st.title("🎙️ Jarvis Mobile")

# --- SIDEBAR: MODEL CHECKER ---
with st.sidebar:
    st.header("🤖 System Status")
    if st.button("Check Available Models"):
        st.write("Fetching models...")
        try:
            found_models = []
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    found_models.append(m.name)
            st.success(f"Access Confirmed! Found {len(found_models)} models.")
            st.code("\n".join(found_models), language="text")
        except Exception as e:
            st.error(f"Error fetching models: {e}")

# --- MAIN INTERFACE ---
audio_value = st.audio_input("Tap to Speak")

if audio_value:
    with st.spinner("Thinking..."):
        audio_bytes = audio_value.read()
        reply = run_agent(audio_bytes)
        
        final_speech = reply
        
        # Check for JSON actions
        if "{" in reply and "action" in reply:
            try:
                json_str = reply[reply.find("{"):reply.rfind("}")+1]
                data = json.loads(json_str)
                
                if data.get("action") == "update":
                    if "new_memory" in data:
                        update_file("Jarvis_Memory.txt", data["new_memory"])
                        st.toast("Memory Saved!", icon="💾")
                    if "new_tasks" in data:
                        update_file("Jarvis_Tasks.txt", data["new_tasks"])
                        st.toast("Tasks Updated!", icon="✅")
                    final_speech = data.get("reply_to_user", "Updated.")
            except:
                pass 
        
        st.chat_message("assistant").write(final_speech)
        
        # Audio Reply
        try:
            audio_file = asyncio.run(text_to_speech(final_speech.replace("*", "")))
            st.audio(audio_file, autoplay=True)
        except:
            pass

# View Memory Debugger
with st.expander("View My Brain"):
    if drive_service:
        st.text_area("Memory", get_file_content("Jarvis_Memory.txt"), height=100)
        st.text_area("Tasks", get_file_content("Jarvis_Tasks.txt"), height=100)
    else:
        st.warning("Drive not connected. Check secrets.")
