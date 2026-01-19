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
st.set_page_config(page_title="Jarvis 2.0", page_icon="⚡", layout="centered")

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

# Try to find the best available Flash model
try:
    model = genai.GenerativeModel('models/gemini-2.0-flash')
except:
    model = genai.GenerativeModel('models/gemini-1.5-flash')

# --- 2. DRIVE FUNCTIONS ---
def get_file_content(filename):
    if not drive_service: return ""
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

# --- 4. THE AGENT ---
def run_agent(user_input, input_type="text"):
    memory = get_file_content("Jarvis_Memory.txt")
    tasks = get_file_content("Jarvis_Tasks.txt")
    today = datetime.datetime.now().strftime("%A, %Y-%m-%d")

    if not memory: memory = "User has not introduced themselves yet."

    sys_prompt = f"""
    SYSTEM: You are Jarvis.
    DATE: {today}
    
    MY LONG TERM MEMORY:
    {memory}
    
    MY CURRENT TASKS:
    {tasks}
    
    INSTRUCTIONS:
    1. Answer the user clearly.
    2. IMPORTANT: If user provides new info, output JSON to update 'Memory'.
    3. IMPORTANT: If plans change, output JSON to update 'Tasks'.
    4. IF NO UPDATE NEEDED: Output JSON with action="none".
    
    OUTPUT FORMAT (ALWAYS JSON):
    {{ "action": "update", "new_memory": "...", "new_tasks": "...", "reply_to_user": "..." }}
    OR
    {{ "action": "none", "reply_to_user": "..." }}
    """
    
    try:
        if input_type == "audio":
            response = model.generate_content([sys_prompt, {"mime_type": "audio/wav", "data": user_input}])
        else:
            response = model.generate_content([sys_prompt, f"USER: {user_input}"])
        return response.text
    except Exception as e:
        return f"AI Error: {e}"

# --- 5. UI LAYOUT ---
st.title("⚡ Jarvis 2.0")

# --- INITIALIZE SESSION STATE ---
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "last_audio" not in st.session_state:
    st.session_state.last_audio = None

# --- DISPLAY HISTORY ---
for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

st.divider()

# --- INPUTS ---
st.subheader("💬 Talk to Jarvis")
audio_val = st.audio_input("🎤 Tap to Speak")

with st.form("text_form", clear_on_submit=True):
    text_val = st.text_input("📝 Or type here:", placeholder="Ex: Plan my day...")
    submitted = st.form_submit_button("Send")

# --- LOGIC ---
final_input = None
input_type = "text"
should_process = False

# CHECK 1: IS IT NEW AUDIO? (Fixes the Loop)
if audio_val is not None:
    if audio_val != st.session_state.last_audio:
        final_input = audio_val.read()
        input_type = "audio"
        st.session_state.last_audio = audio_val # Mark as processed
        should_process = True
    else:
        # It's old audio, do nothing
        should_process = False

# CHECK 2: IS IT NEW TEXT?
elif submitted and text_val:
    final_input = text_val
    input_type = "text"
    should_process = True

# PROCESS
if should_process and final_input:
    # 1. Add User Msg
    if input_type == "text":
        st.session_state.chat_history.append({"role": "user", "content": final_input})
    else:
        st.session_state.chat_history.append({"role": "user", "content": "🎤 [Audio Message]"})
        
    # 2. Run AI
    with st.spinner("Processing..."):
        reply = run_agent(final_input, input_type)
        
        display_text = reply # Default fallback
        
        # 3. Clean JSON Output (Fixes the ugly response)
        if "{" in reply and "reply_to_user" in reply:
            try:
                # Extract JSON part even if there is extra text
                json_str = reply[reply.find("{"):reply.rfind("}")+1]
                data = json.loads(json_str)
                
                # ALWAYS use the clean text from JSON
                if "reply_to_user" in data:
                    display_text = data["reply_to_user"]
                
                # Handle Database Updates
                if data.get("action") == "update":
                    if "new_memory" in data:
                        update_file("Jarvis_Memory.txt", data["new_memory"])
                        st.toast("Brain Updated!", icon="🧠")
                    if "new_tasks" in data:
                        update_file("Jarvis_Tasks.txt", data["new_tasks"])
                        st.toast("Tasks Updated!", icon="✅")
            except:
                pass # Failed to parse, show raw text

        # 4. Add AI Msg
        st.session_state.chat_history.append({"role": "assistant", "content": display_text})
        
        # 5. Rerun to update UI
        st.rerun()

# --- PLAY AUDIO (After Rerun) ---
if st.session_state.chat_history:
    last_msg = st.session_state.chat_history[-1]
    if last_msg["role"] == "assistant":
        if "last_spoken_text" not in st.session_state or st.session_state.last_spoken_text != last_msg["content"]:
            try:
                clean_text = last_msg["content"].replace("*", "")
                audio_file = asyncio.run(text_to_speech(clean_text))
                st.audio(audio_file, autoplay=True)
                st.session_state.last_spoken_text = last_msg["content"]
            except:
                pass

# --- BRAIN VIEW ---
with st.expander("🧠 View Live Memory"):
    if drive_service:
        st.text_area("Memory", get_file_content("Jarvis_Memory.txt"), height=100)
        st.text_area("Tasks", get_file_content("Jarvis_Tasks.txt"), height=100)
