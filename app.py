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
st.set_page_config(page_title="My AI Jarvis", layout="wide")

# 1. Setup Database (Firebase)
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

# --- MODEL SELECTION (SAFE MODE) ---
# We use 'gemini-pro' because it is the most widely available v1.0 model.
# If this works, we can try upgrading to Flash later.
model_name = 'gemini-pro' 
model = genai.GenerativeModel(model_name)

# --- FUNCTIONS ---

def google_search(query):
    """Official Google Custom Search API"""
    if "GOOGLE_SEARCH_KEY" not in st.secrets or "GOOGLE_SEARCH_CX" not in st.secrets:
        return "Error: Google Search Keys missing in Secrets."
    
    try:
        service = build("customsearch", "v1", developerKey=st.secrets["GOOGLE_SEARCH_KEY"])
        # num=4 fetches top 4 results
        result = service.cse().list(q=query, cx=st.secrets["GOOGLE_SEARCH_CX"], num=4).execute()
        
        items = result.get('items', [])
        if not items:
            return "No results found on Google."
            
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

def get_memories():
    if not db: return []
    try:
        docs = db.collection('memories').stream()
        return [doc.to_dict().get('text') for doc in docs]
    except: return []

def add_memory(text):
    if db: db.collection('memories').add({'text': text, 'timestamp': firestore.SERVER_TIMESTAMP})

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
    st.header("Upload File")
    uploaded_file = st.file_uploader("Context", type=["pdf", "png", "jpg", "txt"])
    st.divider()

    st.header("🔧 Diagnostics")
    if st.button("Check Available Models"):
        try:
            available_models = []
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    available_models.append(m.name)
            st.success("Your Key can access these models:")
            st.code("\n".join(available_models))
        except Exception as e:
            st.error(f"Error checking models: {e}")

    st.header("📅 Calendar")
    if st.button("Refresh Events"):
        st.rerun()
    
    events_text = get_calendar_events()
    st.caption("Upcoming meetings:")
    st.text(events_text) 
    st.divider()

    st.header("🧠 Memory Bank")
    if db is not None:
        with st.expander("View Saved Memories"):
            try:
                docs = db.collection("memories").stream()
                found_any = False
                for doc in docs:
                    found_any = True
                    data = doc.to_dict()
                    st.info(f"📝 {data.get('text', 'Unknown')}")
                
                if not found_any:
                    st.write("No memories saved yet.")
            except Exception as e:
                st.error(f"Error reading memories: {e}")
    else:
        st.error("⚠️ Database Not Connected. Check Secrets.")
# --- SIDEBAR END ---

# Chat Logic
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
    india_time = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    
    sys_prompt = f"""
    SYSTEM: You are a personal assistant.
    USER MEMORIES: {memories}
    CALENDAR: {calendar_data}
    TODAY'S DATE: {india_time.strftime("%d %B %Y")}
    
    INSTRUCTIONS:
    1. If user asks for REAL-TIME info (news, prices, research), output JSON:
       {{"action": "search", "query": "The search keywords"}}
       IMPORTANT: Do NOT include specific dates (like "16 January") in the query unless the user asks for history.
       
    2. If user wants to SCHEDULE meeting, output JSON:
       {{"action": "schedule", "summary": "Meeting Name", "time": "YYYY-MM-DDTHH:MM:SS"}}
       
    3. If user wants to SAVE MEMORY, output JSON:
       {{"action": "save_memory", "text": "The fact to save"}}
       
    4. Else answer normally.
    """
    
    full_prompt = [f"{sys_prompt} \n USER: {user_input}"]
    if file_data:
        if isinstance(file_data, Image.Image): full_prompt.append(file_data)
        else: full_prompt[0] += f"\nFILE: {file_data}"
    
    # 3. First Attempt (Ask Gemini)
    reply = ask_gemini(full_prompt)
    
    # 4. Action Handler (The "Double Hop")
    final_response = reply
    
    # Check if the AI wants to take an action
    if "{" in reply and "action" in reply:
        try:
            # Clean up JSON
            clean_json = reply.replace("```json", "").replace("```", "").strip()
            data = json.loads(clean_json)
            
            # --- ACTION: SEARCH (GOOGLE) ---
            if data["action"] == "search":
                with st.chat_message("assistant"):
                    with st.status(f"🔎 Google Search: {data['query']}...", expanded=True) as status:
                        # 1. Run the Google Search
                        search_result = google_search(data["query"])
                        status.write("Reading results...")
                        
                        # 2. Feed results back to Gemini (Round 2)
                        research_prompt = f"""
                        {sys_prompt}
                        USER ASKED: {user_input}
                        SEARCH TOOL RESULT: {search_result}
                        
                        INSTRUCTION: Answer the user's question using the SEARCH RESULT above.
                        """
                        final_response = ask_gemini(research_prompt)
                        status.update(label="✅ Found it!", state="complete", expanded=False)

            # --- ACTION: CALENDAR ---
            elif data["action"] == "schedule":
                final_response = add_calendar_event(data["summary"], data["time"])
            
            # --- ACTION: MEMORY ---
            elif data["action"] == "save_memory":
                add_memory(data["text"])
                final_response = f"🧠 Saved memory: {data['text']}"
                
        except Exception as e:
            pass

    # 5. Output Final Answer
    with st.chat_message("assistant"):
        st.write(final_response)
        if "⚠️" not in final_response and "System Error" not in final_response:
            audio_path = asyncio.run(speak(final_response.replace("*", "")))
            st.audio(audio_path, autoplay=True)
    
    st.session_state.messages.append({"role": "assistant", "content": final_response})
