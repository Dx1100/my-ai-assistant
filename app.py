import streamlit as st
import google.generativeai as genai
import firebase_admin
from firebase_admin import credentials, firestore
import edge_tts
import asyncio
import json
import tempfile
import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from PIL import Image

# --- CONFIGURATION ---
st.set_page_config(page_title="Jarvis AI", page_icon="🤖", layout="wide")

# 1. Setup Database
if "FIREBASE_KEY" in st.secrets:
    key_info = st.secrets["FIREBASE_KEY"]
    if isinstance(key_info, str):
        try: key_dict = json.loads(key_info)
        except: st.stop()
    else: key_dict = dict(key_info)
    
    try: app = firebase_admin.get_app()
    except ValueError: 
        cred = credentials.Certificate(key_dict)
        app = firebase_admin.initialize_app(cred)
    db = firestore.client()
else: db = None

# 2. Setup Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']
cal_service = None
CALENDAR_EMAIL = 'mybusiness110010@gmail.com' 

if "GOOGLE_CALENDAR_KEY" in st.secrets:
    try:
        cal_info = st.secrets["GOOGLE_CALENDAR_KEY"]
        if isinstance(cal_info, str): cal_creds_dict = json.loads(cal_info)
        else: cal_creds_dict = dict(cal_info)
        creds = service_account.Credentials.from_service_account_info(cal_creds_dict, scopes=SCOPES)
        cal_service = build('calendar', 'v3', credentials=creds)
    except: pass

# 3. Setup Brain
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

model_name = 'models/gemini-2.0-flash'
model = genai.GenerativeModel(model_name)

# --- CORE FUNCTIONS ---

def google_search(query):
    if "GOOGLE_SEARCH_KEY" not in st.secrets: return "Error: No Search Keys"
    try:
        service = build("customsearch", "v1", developerKey=st.secrets["GOOGLE_SEARCH_KEY"])
        result = service.cse().list(q=query, cx=st.secrets["GOOGLE_SEARCH_CX"], num=4).execute()
        items = result.get('items', [])
        if not items: return "No results."
        return "\n".join([f"Title: {i['title']}\nSnippet: {i['snippet']}\n" for i in items])
    except Exception as e: return f"Search Error: {e}"

def get_calendar_events():
    if not cal_service: return "Calendar disconnected."
    try:
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events = cal_service.events().list(calendarId=CALENDAR_EMAIL, timeMin=now, maxResults=5, singleEvents=True, orderBy='startTime').execute().get('items', [])
        if not events: return "No events."
        return "\n".join([f"📅 {e['start'].get('dateTime', e['start'].get('date'))}: {e['summary']}" for e in events])
    except: return "Calendar Error"

def add_calendar_event(summary, start_time_str):
    if not cal_service: return "Calendar disconnected."
    try:
        start_dt = datetime.datetime.fromisoformat(start_time_str)
        end_dt = start_dt + datetime.timedelta(hours=1)
        event = {'summary': summary, 'start': {'dateTime': start_dt.isoformat()}, 'end': {'dateTime': end_dt.isoformat()}}
        cal_service.events().insert(calendarId=CALENDAR_EMAIL, body=event).execute()
        return f"✅ Scheduled: {summary}"
    except: return "Scheduling Failed"

def get_memories():
    if not db: return []
    try:
        docs = db.collection('memories').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(5).stream()
        return [doc.to_dict().get('text') for doc in docs]
    except: return []

def add_memory(text):
    if db: db.collection('memories').add({'text': text, 'timestamp': firestore.SERVER_TIMESTAMP})

async def speak(text):
    communicate = edge_tts.Communicate(text, "en-IN-NeerjaNeural")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
        await communicate.save(fp.name)
        return fp.name

def transcribe_audio(audio_file):
    # Use Gemini to listen to the audio file and transcribe it
    try:
        audio_file.seek(0)
        audio_bytes = audio_file.read()
        prompt = "Listen to this audio exactly and transcribe it word for word. Do not add any commentary."
        response = model.generate_content([prompt, {"mime_type": "audio/wav", "data": audio_bytes}])
        return response.text
    except Exception as e:
        return f"Error listening: {e}"

def ask_gemini(prompt_parts):
    try:
        response = model.generate_content(prompt_parts)
        return response.text
    except Exception as e: return f"Error: {e}"

# --- UI LAYOUT ---
st.title("🤖 Jarvis Mobile")

# Voice Input (The New Power)
audio_value = st.audio_input("🎙️ Tap to Speak")

if "messages" not in st.session_state: st.session_state.messages = []

# Display Chat
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]): st.write(msg["content"])

# Handle Inputs (Voice OR Text)
user_text = st.chat_input("Type instruction...")
final_input = None

if audio_value:
    with st.spinner("Listening..."):
        transcribed_text = transcribe_audio(audio_value)
        final_input = transcribed_text
elif user_text:
    final_input = user_text

# PROCESSING LOGIC
if final_input:
    st.session_state.messages.append({"role": "user", "content": final_input})
    with st.chat_message("user"): st.write(final_input)

    # Context Building
    memories = get_memories()
    calendar_data = get_calendar_events()
    history = st.session_state.messages[-10:]
    
    sys_prompt = f"""
    SYSTEM: You are Jarvis. concise, efficient, helpful.
    MEMORIES: {memories}
    CALENDAR: {calendar_data}
    DATE: {datetime.datetime.now().strftime("%d %B %Y")}
    HISTORY: {history}
    
    TOOLS:
    - Real-time info -> output JSON: {{"action": "search", "query": "..."}}
    - Schedule -> output JSON: {{"action": "schedule", "summary": "...", "time": "..."}}
    - Save Memory -> output JSON: {{"action": "save_memory", "text": "..."}}
    """
    
    reply = ask_gemini(sys_prompt + f"\nUSER: {final_input}")
    
    # Action Handling
    if "{" in reply and "action" in reply:
        try:
            data = json.loads(reply.replace("```json", "").replace("```", "").strip())
            if data["action"] == "search":
                with st.status("🔎 Searching Google...", expanded=True):
                    res = google_search(data["query"])
                    reply = ask_gemini(f"{sys_prompt}\nSEARCH RESULT: {res}\nUSER ASKED: {final_input}")
            elif data["action"] == "schedule":
                reply = add_calendar_event(data["summary"], data["time"])
            elif data["action"] == "save_memory":
                add_memory(data["text"])
                reply = "🧠 Memory Saved."
        except: pass

    # Output
    with st.chat_message("assistant"):
        st.write(reply)
        if len(reply) < 300: # Speak if short enough
             try:
                audio = asyncio.run(speak(reply.replace("*", "")))
                st.audio(audio, autoplay=True)
             except: pass
    
    st.session_state.messages.append({"role": "assistant", "content": reply})
