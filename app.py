import streamlit as st
import google.generativeai as genai
import firebase_admin
from firebase_admin import credentials, firestore
import edge_tts
import asyncio
import json
import tempfile
import time
import PyPDF2
from PIL import Image
import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- CONFIGURATION ---
st.set_page_config(page_title="My AI Jarvis", layout="wide", page_icon="🎙️")

# 1. Setup Database (Firebase - Long Term Memory)
if "FIREBASE_KEY" in st.secrets:
    key_info = st.secrets["FIREBASE_KEY"]
    if isinstance(key_info, str):
        try: key_dict = json.loads(key_info)
        except: st.stop()
    else: key_dict = dict(key_info)
    
    try: 
        app = firebase_admin.get_app()
    except ValueError: 
        cred = credentials.Certificate(key_dict)
        app = firebase_admin.initialize_app(cred)
    
    db = firestore.client()
else: 
    db = None

# 2. Setup Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']
cal_service = None
CALENDAR_EMAIL = 'mybusiness110010@gmail.com' 

if "GOOGLE_CALENDAR_KEY" in st.secrets:
    try:
        cal_info = st.secrets["GOOGLE_CALENDAR_KEY"]
        if isinstance(cal_info, str):
            cal_creds_dict = json.loads(cal_info)
        else:
            cal_creds_dict = dict(cal_info)
            
        creds = service_account.Credentials.from_service_account_info(
            cal_creds_dict, scopes=SCOPES
        )
        cal_service = build('calendar', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"Calendar Error: {e}")

# 3. Setup Brain (Gemini)
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

# USE THE SMART MODEL (Gemini 2.0 Flash)
model_name = 'models/gemini-2.0-flash'
model = genai.GenerativeModel(model_name)

# --- FUNCTIONS ---

def google_search(query):
    """Official Google Custom Search API"""
    if "GOOGLE_SEARCH_KEY" not in st.secrets or "GOOGLE_SEARCH_CX" not in st.secrets:
        return "Error: Google Search Keys missing in Secrets."
    
    try:
        service = build("customsearch", "v1", developerKey=st.secrets["GOOGLE_SEARCH_KEY"])
        result = service.cse().list(q=query, cx=st.secrets["GOOGLE_SEARCH_CX"], num=4).execute()
        items = result.get('items', [])
        if not items: return "No results found on Google."
            
        search_data = ""
        for item in items:
            title = item.get('title', 'No Title')
            snippet = item.get('snippet', 'No Snippet')
            link = item.get('link', 'No Link')
            search_data += f"Title: {title}\nSnippet: {snippet}\nLink: {link}\n\n"
        return search_data
    except Exception as e:
        return f"Google Search Error: {e}"

def get_calendar_events():
    if not cal_service: return "Calendar not connected."
    try:
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events_result = cal_service.events().list(
            calendarId=CALENDAR_EMAIL, timeMin=now,
            maxResults=5, singleEvents=True,
            orderBy='startTime').execute()
        events = events_result.get('items', [])
        if not events: return "No upcoming events found."
        
        event_list = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            event_list.append(f"📅 {start}: {event['summary']}")
        return "\n".join(event_list)
    except Exception as e:
        return f"Error reading calendar: {e}"

def add_calendar_event(summary, start_time_str):
    if not cal_service: return "Calendar not connected."
    try:
        start_dt = datetime.datetime.fromisoformat(start_time_str)
        end_dt = start_dt + datetime.timedelta(hours=1)
        event = {
            'summary': summary,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Kolkata'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Asia/Kolkata'},
        }
        cal_service.events().insert(calendarId=CALENDAR_EMAIL, body=event).execute()
        return f"✅ Scheduled '{summary}' for {start_time_str}"
    except Exception as e:
        return f"Failed to schedule: {e}"

# --- MEMORY FUNCTIONS (THE SECOND BRAIN) ---
def get_memories():
    if not db: return []
    try:
        # Fetch all memories
        docs = db.collection('memories').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(10).stream()
        return [doc.to_dict().get('text') for doc in docs]
    except: return []

def add_memory(text):
    if db: 
        db.collection('memories').add({
            'text': text, 
            'timestamp': firestore.SERVER_TIMESTAMP
        })

async def speak(text):
    communicate = edge_tts.Communicate(text, "en-IN-NeerjaNeural")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
        await communicate.save(fp.name)
        return fp.name

# --- VOICE FUNCTION ---
def transcribe_audio(audio_file):
    try:
        audio_file.seek(0)
        audio_bytes = audio_file.read()
        prompt = "Listen to this audio exactly and transcribe it word for word. Do not add any commentary."
        response = model.generate_content([prompt, {"mime_type": "audio/wav", "data": audio_bytes}])
        return response.text
    except Exception as e:
        return f"Error listening: {e}"

def ask_gemini(prompt):
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"System Error: {str(e)}"

def process_file(uploaded):
    try:
        if uploaded.type in ["image/png", "image/jpeg", "image/jpg"]:
            return Image.open(uploaded)
        elif uploaded.type == "application/pdf":
            reader = PyPDF2.PdfReader(uploaded)
            return "".join([p.extract_text() for p in reader.pages])
        else:
            return uploaded.getvalue().decode("utf-8")
    except Exception as e:
        return f"Error reading file: {str(e)}"

# --- UI ---
st.title("🤖 My AI Jarvis (Second Brain)")

# --- SIDEBAR (RESTORED CALENDAR & MEMORY) ---
with st.sidebar:
    st.header("📂 Upload Context")
    uploaded_file = st.file_uploader("File", type=["pdf", "png", "jpg", "txt"])
    st.divider()

    # --- CALENDAR SECTION (ADDED BACK) ---
    st.header("📅 Calendar")
    if st.button("Refresh Events"):
        st.rerun()
    
    events_text = get_calendar_events()
    st.caption("Upcoming meetings:")
    st.text(events_text) 
    st.divider()

    # --- MEMORY SECTION ---
    st.header("🧠 Long-Term Memory")
    if db:
        with st.expander("View Recent Memories"):
            mems = get_memories()
            for m in mems:
                st.info(f"📝 {m}")
    else:
        st.error("Database Disconnected")

# --- CHAT LOGIC ---
if "messages" not in st.session_state: st.session_state.messages = []

# 1. Voice Input
audio_value = st.audio_input("🎙️ Tap to Speak")

# 2. Display History
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]): st.write(msg["content"])

# 3. Handle New Input (Text OR Voice)
user_text = st.chat_input("Type instruction...")
final_input = None

# Logic: If audio is present, use that. Otherwise use text.
if audio_value:
    with st.spinner("Listening..."):
        transcribed_text = transcribe_audio(audio_value)
        final_input = transcribed_text
elif user_text:
    final_input = user_text

# 4. Process The Input
if final_input:
    # A. Display User Message
    st.session_state.messages.append({"role": "user", "content": final_input})
    with st.chat_message("user"): st.write(final_input)

    # B. Gather Context
    memories = get_memories()
    calendar_data = get_calendar_events()
    file_data = process_file(uploaded_file) if uploaded_file else None
    
    # C. Build Conversation History
    history_str = ""
    for msg in st.session_state.messages[-15:]: 
        history_str += f"{msg['role'].upper()}: {msg['content']}\n"
    
    india_time = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    
    sys_prompt = f"""
    SYSTEM: You are Jarvis, a personal AI assistant and Second Brain.
    
    --- YOUR SECOND BRAIN (PERMANENT KNOWLEDGE) ---
    The user has explicitly saved these facts. Use them to answer questions:
    {memories}
    
    --- CALENDAR ---
    {calendar_data}
    
    --- CURRENT CONTEXT (SHORT TERM MEMORY) ---
    {history_str}
    
    TODAY'S DATE: {india_time.strftime("%d %B %Y")}
    
    INSTRUCTIONS:
    1. If user asks for REAL-TIME info, output JSON: {{"action": "search", "query": "..."}}
    2. If user wants to SCHEDULE meeting, output JSON: {{"action": "schedule", "summary": "...", "time": "..."}}
    3. If user says "Save this" or "Remember that", output JSON: {{"action": "save_memory", "text": "The exact info to save"}}
    4. Otherwise, answer helpfully using the HISTORY and SECOND BRAIN.
    """
    
    # Construct Full Prompt
    prompt_payload = [sys_prompt]
    if file_data:
        if isinstance(file_data, Image.Image): prompt_payload.append(file_data)
        else: prompt_payload[0] += f"\nFILE CONTENT: {file_data}"
    
    # D. First Attempt
    reply = ask_gemini(prompt_payload)
    
    # E. Action Handler
    final_response = reply
    
    if "{" in reply and "action" in reply:
        try:
            clean_json = reply.replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_json)
            
            if data["action"] == "search":
                with st.chat_message("assistant"):
                    with st.status(f"🔎 Google Search: {data['query']}...", expanded=True) as status:
                        search_result = google_search(data["query"])
                        status.write("Reading results...")
                        
                        research_prompt = f"""
                        {sys_prompt}
                        SEARCH TOOL RESULT: {search_result}
                        INSTRUCTION: Answer based on the SEARCH RESULT.
                        """
                        final_response = ask_gemini(research_prompt)
                        status.update(label="✅ Found it!", state="complete", expanded=False)

            elif data["action"] == "schedule":
                final_response = add_calendar_event(data["summary"], data["time"])
            
            elif data["action"] == "save_memory":
                add_memory(data["text"])
                final_response = f"🧠 I have saved this to your Second Brain: '{data['text']}'"
                
        except Exception as e:
            pass

    # F. Output
    with st.chat_message("assistant"):
        st.write(final_response)
        if len(final_response) > 20 and "Warning" not in final_response: 
            try:
                audio_path = asyncio.run(speak(final_response.replace("*", "")))
                st.audio(audio_path, autoplay=True)
            except: pass
    
    st.session_state.messages.append({"role": "assistant", "content": final_response})
