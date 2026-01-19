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

# --- UPDATED MODEL TO GEMINI 2.0 FLASH ---
# We try the standard stable tag first. 
# If this fails, use the 'check_models.py' script below to find your exact tag.
try:
    model = genai.GenerativeModel('models/gemini-2.0-flash')
except:
    # Fallback to experimental if stable isn't fully rolled out to your key
    model = genai.GenerativeModel('models/gemini-2.0-flash-exp')

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
    # 'en-US-AriaNeural' is a fast, crisp voice
    communicate = edge_tts.Communicate(text, "en-US-AriaNeural")
    out_file = "reply.mp3"
    await communicate.save(out_file)
    return out_file

# --- 4. THE AGENT ---
def run_agent(user_input, input_type="text"):
    memory = get_file_content("Jarvis_Memory.txt")
    tasks = get_file_content("Jarvis_Tasks.txt")
    today = datetime.datetime.now().strftime("%A, %Y-%m-%d")

    # If memory is empty, we give it a hint to welcome the user
    if not memory:
        memory = "User has not introduced themselves yet."

    sys_prompt = f"""
    SYSTEM: You are Jarvis, powered by Gemini 2.0 Flash.
    DATE: {today}
    
    MY LONG TERM MEMORY:
    {memory}
    
    MY CURRENT TASKS:
    {tasks}
    
    INSTRUCTIONS:
    1. Answer the user clearly and concisely.
    2. IMPORTANT: If the user provides new info, output JSON to update 'Memory'.
    3. IMPORTANT: If plans change, output JSON to update 'Tasks'.
    
    OUTPUT FORMAT:
    {{ "action": "update", "new_memory": "...", "new_tasks": "...", "reply_to_user": "..." }}
    OR just plain text if no update needed.
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

# Session State for Chat History
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display Chat History
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

# --- INPUT AREA ---
# We put inputs in columns so they look like a control panel
col1, col2 = st.columns([1, 4])

with col1:
    audio_val = st.audio_input("🎤 Speak")

with col2:
    text_val = st.chat_input("Type your message here...")

# Handle Input
final_input = None
input_type = "text"

if audio_val:
    final_input = audio_val.read()
    input_type = "audio"
elif text_val:
    final_input = text_val
    input_type = "text"

if final_input:
    # Add user message to history (if text)
    if input_type == "text":
        st.session_state.messages.append({"role": "user", "content": final_input})
        with st.chat_message("user"):
            st.write(final_input)
    else:
        with st.chat_message("user"):
            st.write("🎤 [Audio Message]")

    with st.spinner("Thinking..."):
        reply = run_agent(final_input, input_type)
        
        display_text = reply
        
        # Parse JSON if update happened
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

        # Display AI Response
        st.session_state.messages.append({"role": "assistant", "content": display_text})
        with st.chat_message("assistant"):
            st.write(display_text)

        # Audio Reply
        try:
            audio_file = asyncio.run(text_to_speech(display_text.replace("*", "")))
            st.audio(audio_file, autoplay=True)
        except:
            pass

# --- BRAIN MONITOR ---
with st.expander("🧠 View Live Memory (Drive Data)"):
    mem = get_file_content("Jarvis_Memory.txt")
    tsk = get_file_content("Jarvis_Tasks.txt")
    
    if not mem:
        st.info("Memory is empty. Introduce yourself to start saving data!")
    else:
        st.text_area("Long Term Memory", mem, height=150)
        st.text_area("Current Tasks", tsk, height=150)
