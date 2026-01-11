import streamlit as st
import google.generativeai as genai
import firebase_admin
from firebase_admin import credentials, firestore
from duckduckgo_search import DDGS
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
st.set_page_config(page_title="My AI Jarvis", layout="wide")

# --- DEBUG LINES (Add these 2 lines) ---
st.write("🔑 **I see these keys in your Secrets:**", list(st.secrets.keys()))
st.write("Is GOOGLE_CALENDAR_KEY in there?", "GOOGLE_CALENDAR_KEY" in st.secrets)
# ---------------------------------------

# 1. Setup Database (Firebase)
if "FIREBASE_KEY" in st.secrets:
    key_info = st.secrets["FIREBASE_KEY"]
    if isinstance(key_info, str):
        try: key_dict = json.loads(key_info)
        except: st.stop()
    else: key_dict = dict(key_info)
    
    cred = credentials.Certificate(key_dict)
    try: firebase_admin.get_app()
    except ValueError: firebase_admin.initialize_app(cred)
    db = firestore.client()
else: db = None

# 2. Setup Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']
cal_service = None

# --- ENTER YOUR EMAIL HERE ---
CALENDAR_EMAIL = 'mybusiness110010@gmail.com'  # <--- CHANGE THIS!!!!

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

model_name = 'gemini-flash-latest'
model = genai.GenerativeModel(model_name)

# --- FUNCTIONS ---

def get_calendar_events():
    """Fetch next 5 upcoming events"""
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
    """
    Adds an event. Expects start_time_str in ISO format (YYYY-MM-DDTHH:MM:SS)
    """
    if not cal_service: return "Calendar not connected."
    try:
        # Simple parser: assumes 1 hour duration
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

def get_memories():
    if not db: return []
    try:
        docs = db.collection('memories').stream()
        return [doc.to_dict().get('fact') for doc in docs]
    except: return []

def add_memory(fact):
    if db: db.collection('memories').add({'fact': fact, 'timestamp': firestore.SERVER_TIMESTAMP})

def web_search(query):
    try: return str(DDGS().text(query, max_results=3))
    except: return "No internet."

async def speak(text):
    communicate = edge_tts.Communicate(text, "en-IN-NeerjaNeural")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
        await communicate.save(fp.name)
        return fp.name

def ask_gemini(prompt):
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"System Error: {str(e)}"

def process_file(uploaded):
    """Universal File Handler"""
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
st.title("🤖 My AI Jarvis")

# --- SIDEBAR START ---
    with st.sidebar:
        # 1. File Uploader (Vision)
        st.header("Upload File")
        uploaded_file = st.file_uploader("Context", type=["pdf", "png", "jpg", "txt"])
        st.divider()

        # 2. Calendar Section
        st.header("📅 Calendar")
        if st.button("Refresh Events"):
            st.rerun()
        
        # Show events
        events_text = get_calendar_events()
        st.caption("Upcoming meetings:")
        st.text(events_text) # Using st.text keeps the formatting clean
        st.divider()

        # 3. Long-Term Memory Section (NEW)
        st.header("🧠 Memory Bank")
        
        # Only try to fetch if we are connected to Firebase
        if db is not None:
            # Use an expander so it doesn't clutter the screen
            with st.expander("View Saved Memories"):
                try:
                    docs = db.collection("memories").stream()
                    found_any = False
                    for doc in docs:
                        found_any = True
                        data = doc.to_dict()
                        # Display each memory in a little box
                        st.info(f"📝 {data.get('text', 'Unknown')}")
                    
                    if not found_any:
                        st.write("No memories saved yet.")
                        
                except Exception as e:
                    st.error(f"Error reading memories: {e}")
        else:
            st.error("⚠️ Database Not Connected. Check Secrets.")
    # --- SIDEBAR END ---
# Chat
if "messages" not in st.session_state: st.session_state.messages = []
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]): st.write(msg["content"])

user_input = st.chat_input("Type instruction...")

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"): st.write(user_input)

    # 1. Gather Context
    memories = get_memories()
    calendar_data = get_calendar_events()
    file_data = process_file(uploaded_file) if uploaded_file else None
    
    # 2. Construct Prompt
    # --- FIX: CALCULATE INDIA TIME ---
    india_time = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    # ---------------------------------

    sys_prompt = f"""
    SYSTEM: You are a personal assistant.
    USER MEMORIES: {memories}
    CALENDAR: {calendar_data}
    
    INSTRUCTIONS:
    1. If user wants to SCHEDULE meeting, output JSON:
       {{"action": "schedule", "summary": "Meeting Name", "time": "YYYY-MM-DDTHH:MM:SS"}}
       (Current Date & Time in India: {india_time.strftime("%Y-%m-%d %H:%M:%S")})
       IMPORTANT: The user is in India (IST). Use the current India time above to calculate dates.
       
    2. If user wants to SAVE MEMORY, output JSON:
       {{"action": "save_memory", "text": "The fact to save"}}
       
    3. Else answer normally as a helpful assistant.

    """
    
    full_prompt = [f"{sys_prompt} \n USER: {user_input}"]
    if file_data:
        if isinstance(file_data, Image.Image): full_prompt.append(file_data)
        else: full_prompt[0] += f"\nFILE: {file_data}"
    
    # 3. Get Answer
    reply = ask_gemini(full_prompt)
    
    # 4. Check for Actions (JSON)
    final_response = reply
    if "{" in reply and "action" in reply:
        try:
            clean_json = reply.replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_json)
            
            if data["action"] == "schedule":
                final_response = add_calendar_event(data["summary"], data["time"])
            elif data["action"] == "memory":
                add_memory(data["text"])
                final_response = f"🧠 Saved memory: {data['text']}"
        except: pass

    # 5. Output
    with st.chat_message("assistant"):
        st.write(final_response)
        if "⚠️" not in final_response:
            audio_path = asyncio.run(speak(final_response.replace("*", "")))
            st.audio(audio_path, autoplay=True)
    
    st.session_state.messages.append({"role": "assistant", "content": final_response})
