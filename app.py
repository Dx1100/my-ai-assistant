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
import PyPDF2

# --- CONFIGURATION ---
st.set_page_config(page_title="Jarvis Research", layout="wide", page_icon="🧠")

# 1. Setup Database
if "FIREBASE_KEY" in st.secrets:
    try:
        key_dict = json.loads(st.secrets["FIREBASE_KEY"]) if isinstance(st.secrets["FIREBASE_KEY"], str) else dict(st.secrets["FIREBASE_KEY"])
        try: app = firebase_admin.get_app()
        except ValueError: 
            cred = credentials.Certificate(key_dict)
            app = firebase_admin.initialize_app(cred)
        db = firestore.client()
    except: db = None
else: db = None

# 2. Setup Google Calendar (Restored)
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
model = genai.GenerativeModel('models/gemini-2.0-flash')

# --- RESEARCH TOOLS (UPDATED) ---

def google_search(query):
    """Web Search with Citation Formatting"""
    if "GOOGLE_SEARCH_KEY" not in st.secrets: return "Error: No Search Keys"
    try:
        service = build("customsearch", "v1", developerKey=st.secrets["GOOGLE_SEARCH_KEY"])
        result = service.cse().list(q=query, cx=st.secrets["GOOGLE_SEARCH_CX"], num=5).execute()
        items = result.get('items', [])
        if not items: return "No results."
        
        # Format for AI to read and cite
        formatted_results = ""
        for i in items:
            formatted_results += f"Source: {i['title']}\nURL: {i['link']}\nSnippet: {i['snippet']}\n\n"
        return formatted_results
    except Exception as e: return f"Search Failed: {e}"

def search_youtube(query):
    """Specific YouTube Search"""
    if "GOOGLE_SEARCH_KEY" not in st.secrets: return "Error: No Search Keys"
    try:
        # We append 'site:youtube.com' to force video results
        youtube_query = f"{query} site:youtube.com"
        service = build("customsearch", "v1", developerKey=st.secrets["GOOGLE_SEARCH_KEY"])
        result = service.cse().list(q=youtube_query, cx=st.secrets["GOOGLE_SEARCH_CX"], num=3).execute()
        items = result.get('items', [])
        if not items: return "No videos found."
        
        video_data = ""
        for i in items:
            video_data += f"🎥 Video: {i['title']}\n🔗 Link: {i['link']}\n\n"
        return video_data
    except Exception as e: return f"Video Search Failed: {e}"

# --- CALENDAR & MEMORY TOOLS ---
def get_calendar_events():
    if not cal_service: return "Calendar disconnected."
    try:
        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events = cal_service.events().list(calendarId=CALENDAR_EMAIL, timeMin=now, maxResults=5, singleEvents=True, orderBy='startTime').execute().get('items', [])
        if not events: return "No events."
        return "\n".join([f"📅 {e['start'].get('dateTime', e['start'].get('date'))}: {e['summary']}" for e in events])
    except: return "Calendar Error"

def get_memories():
    if not db: return []
    try:
        docs = db.collection('memories').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(5).stream()
        return [doc.to_dict().get('text') for doc in docs]
    except: return []

def add_memory(text):
    if db: db.collection('memories').add({'text': text, 'timestamp': firestore.SERVER_TIMESTAMP})

def transcribe_audio(audio_file):
    try:
        audio_file.seek(0)
        prompt = "Transcribe exactly."
        response = model.generate_content([prompt, {"mime_type": "audio/wav", "data": audio_file.read()}])
        return response.text
    except: return "Error listening."

async def speak(text):
    communicate = edge_tts.Communicate(text, "en-IN-NeerjaNeural")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
        await communicate.save(fp.name)
        return fp.name

def ask_gemini(prompt_parts):
    try:
        return model.generate_content(prompt_parts).text
    except Exception as e: return f"Error: {e}"

# --- UI LAYOUT ---
st.title("🤖 Jarvis Research Agent")

# --- SIDEBAR (RESTORED) ---
with st.sidebar:
    st.header("📅 Calendar")
    if st.button("Refresh Events"): st.rerun()
    st.text(get_calendar_events())
    
    st.divider()
    
    st.header("🧠 Memory")
    if db:
        with st.expander("View Memories"):
            for m in get_memories(): st.info(m)
    else: st.warning("Database Disconnected")

# --- CHAT LOGIC ---
if "messages" not in st.session_state: st.session_state.messages = []

# Initialize Audio State Tracker
if "last_audio" not in st.session_state:
    st.session_state.last_audio = None

# 1. Voice Input (Top)
audio_value = st.audio_input("🎙️ Voice Command")

# 2. Display Chat
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]): st.write(msg["content"])

# 3. Text Input (Bottom)
user_text = st.chat_input("Type instruction...")

# 4. Input Priority Logic (THE SMART SWITCH FIX)
final_input = None

# Case A: New Audio Detected (Prioritize Voice)
if audio_value and audio_value != st.session_state.last_audio:
    st.session_state.last_audio = audio_value # Update tracker
    with st.spinner("Processing Voice..."):
        final_input = transcribe_audio(audio_value)

# Case B: Text Input Detected (Prioritize Text if Audio hasn't changed)
elif user_text:
    final_input = user_text

# 5. Processing
if final_input:
    st.session_state.messages.append({"role": "user", "content": final_input})
    with st.chat_message("user"): st.write(final_input)

    # Context
    memories = get_memories()
    calendar_data = get_calendar_events()
    
    sys_prompt = f"""
    SYSTEM: You are Jarvis.
    MEMORIES: {memories}
    CALENDAR: {calendar_data}
    DATE: {datetime.datetime.now().strftime("%d %B %Y")}
    
    CRITICAL RESEARCH RULES:
    1. If answering from search, YOU MUST CITE SOURCE at the end of the sentence. Format: [Source Name](URL).
    2. If user asks for videos, use the 'search_video' tool.
    
    TOOLS:
    - Search Google -> {{"action": "search", "query": "..."}}
    - Find Videos -> {{"action": "search_video", "query": "..."}}
    - Save Memory -> {{"action": "save_memory", "text": "..."}}
    """
    
    reply = ask_gemini([sys_prompt, f"USER: {final_input}"])
    
    # E. Action Handler (SMARTER VERSION)
    final_response = reply
    
    # Check if there is a JSON command hidden in the text
    if "{" in reply and "action" in reply:
        try:
            # 1. Find the start and end of the JSON object strictly
            start_index = reply.find("{")
            end_index = reply.rfind("}") + 1
            clean_json = reply[start_index:end_index]
            
            # 2. Load it
            data = json.loads(clean_json)
            
            if data["action"] == "search":
                with st.status(f"🔎 Researching: {data['query']}...", expanded=True) as status:
                    res = google_search(data["query"])
                    status.write("Found sources...")
                    
                    # Feed results back to Brain with CITATION instruction
                    research_prompt = f"""
                    {sys_prompt}
                    SEARCH DATA FOUND:
                    {res}
                    
                    USER ORIGINAL REQUEST: {final_input}
                    
                    INSTRUCTION: 
                    1. Answer the user's question using the SEARCH DATA.
                    2. You MUST cite the source name and link for every fact.
                    """
                    final_response = ask_gemini(research_prompt)
                    status.update(label="✅ Done!", state="complete", expanded=False)

            elif data["action"] == "search_video":
                with st.status(f"🎥 Finding Videos: {data['query']}...", expanded=True) as status:
                    res = search_youtube(data["query"])
                    status.write("Found videos...")
                    final_response = f"Here are the top videos I found:\n\n{res}"
                    status.update(label="✅ Found videos!", state="complete", expanded=False)

            elif data["action"] == "save_memory":
                add_memory(data["text"])
                final_response = f"🧠 Memory Saved: {data['text']}"
                
        except Exception as e:
            # If it fails, just print the raw reply so we see what happened
            final_response = f"I tried to run a tool but failed. Raw Error: {e}"
            
    # Output
    with st.chat_message("assistant"):
        st.write(final_response)
        if len(final_response) < 400:
             try:
                audio = asyncio.run(speak(final_response.replace("*", "").replace("http", "")))
                st.audio(audio, autoplay=True)
             except: pass
    
    st.session_state.messages.append({"role": "assistant", "content": final_response})
