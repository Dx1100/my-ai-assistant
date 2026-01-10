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

# --- CONFIGURATION ---
st.set_page_config(page_title="My AI Assistant", layout="wide")

# 1. Setup Database (Firebase)
if "FIREBASE_KEY" in st.secrets:
    key_info = st.secrets["FIREBASE_KEY"]
    if isinstance(key_info, str):
        try:
            key_dict = json.loads(key_info)
        except:
            st.error("Error decoding Firebase Key. Check Secrets.")
            st.stop()
    else:
        key_dict = dict(key_info)

    cred = credentials.Certificate(key_dict)
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(cred)
    db = firestore.client()
else:
    st.warning("⚠️ Database disconnected. Tasks won't save.")
    db = None

# 2. Setup Brain (Gemini)
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.error("Missing Gemini API Key.")
    st.stop()

# USE THE KNOWN AVAILABLE MODEL (From your list)
model_name = 'gemini-2.0-flash-lite' 
model = genai.GenerativeModel(model_name)

# --- FUNCTIONS ---
def get_tasks():
    if not db: return []
    try:
        docs = db.collection('tasks').stream()
        return [f"{doc.id}: {doc.to_dict().get('task')}" for doc in docs]
    except:
        return []

def add_task(task_text):
    if db:
        db.collection('tasks').add({'task': task_text, 'status': 'pending'})

def web_search(query):
    try:
        results = DDGS().text(query, max_results=3)
        return str(results)
    except:
        return "No internet results found."

async def speak(text):
    communicate = edge_tts.Communicate(text, "en-IN-NeerjaNeural")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
        await communicate.save(fp.name)
        return fp.name

def read_file(uploaded):
    if uploaded.name.endswith(".pdf"):
        try:
            reader = PyPDF2.PdfReader(uploaded)
            return "".join([p.extract_text() for p in reader.pages])
        except: return "Error reading PDF"
    return ""

def ask_gemini(prompt):
    """
    CRASH PROTECTION: 
    This function catches the 429 Error and waits automatically 
    instead of crashing the app.
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        err_msg = str(e)
        if "429" in err_msg or "ResourceExhausted" in err_msg:
            return "⚠️ Speed Limit Reached. Please wait 30 seconds and try again."
        elif "NotFound" in err_msg:
            return f"Error: Model {model_name} not found. Please check spelling."
        else:
            return f"System Error: {err_msg}"

# --- UI ---
st.title("🤖 My AI Manager")

with st.sidebar:
    st.header("Upload Context")
    uploaded_file = st.file_uploader("Upload PDF/PNG", type=["pdf", "png"])
    st.divider()
    st.header("My Tasks")
    if st.button("Refresh Tasks"):
        st.rerun()
    
    tasks_list = get_tasks()
    if tasks_list:
        for t in tasks_list:
            st.write(f"• {t}")
    else:
        st.write("No tasks found.")

# Chat
if "messages" not in st.session_state: st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

user_input = st.chat_input("Type instruction...")

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.write(user_input)

    # Context
    context = f"CURRENT TASKS: {tasks_list}\n"
    if uploaded_file: context += f"FILE CONTENT: {read_file(uploaded_file)}\n"
    
    # Logic
    reply = ""
    if "save" in user_input.lower() and "task" in user_input.lower():
        clean_task = user_input.lower().replace("save", "").replace("task", "").strip()
        add_task(clean_task)
        reply = f"✅ Saved task: {clean_task}"
    
    elif "search" in user_input.lower():
        search_res = web_search(user_input)
        prompt = f"Context: {context} \n Web Results: {search_res} \n User: {user_input} \n Answer:"
        reply = ask_gemini(prompt)
        
    else:
        prompt = f"Context: {context} \n User: {user_input} \n Answer:"
        reply = ask_gemini(prompt)

    # Reply
    with st.chat_message("assistant"):
        st.write(reply)
        if "⚠️" not in reply: # Don't speak error messages
            audio_path = asyncio.run(speak(reply.replace("*", "")))
            st.audio(audio_path, autoplay=True)
    
    st.session_state.messages.append({"role": "assistant", "content": reply})
