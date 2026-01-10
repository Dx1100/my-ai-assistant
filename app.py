import streamlit as st
import google.generativeai as genai
import firebase_admin
from firebase_admin import credentials, firestore
from duckduckgo_search import DDGS
import edge_tts
import asyncio
import json
import tempfile
from PIL import Image
import PyPDF2

# --- CONFIGURATION ---
st.set_page_config(page_title="My AI Assistant", layout="wide")

# 1. Setup Database (Firebase)
# We use a trick to handle the JSON key securely in the cloud
if "FIREBASE_KEY" in st.secrets:
    # If the key is stored as a string in secrets, parse it
    key_info = st.secrets["FIREBASE_KEY"]
    # Handle cases where it might be double-encoded
    if isinstance(key_info, str):
        try:
            key_dict = json.loads(key_info)
        except json.JSONDecodeError:
            # If it's not valid JSON, it might be TOML format, try accessing directly if mapped
            st.error("Error decoding Firebase JSON. Check Secrets format.")
            st.stop()
    else:
        # If Streamlit parsed it as a TOML table already
        key_dict = dict(key_info)

    cred = credentials.Certificate(key_dict)
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(cred)
    db = firestore.client()
else:
    st.warning("⚠️ Database not connected. Tasks will not be saved.")
    db = None

# 2. Setup Brain (Gemini)
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.error("Missing Gemini API Key.")
    st.stop()

model = genai.GenerativeModel('gemini-2.0-flash-lite')

# --- FUNCTIONS ---
def get_tasks():
    if not db: return []
    docs = db.collection('tasks').stream()
    return [f"{doc.id}: {doc.to_dict().get('task')}" for doc in docs]

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
        reader = PyPDF2.PdfReader(uploaded)
        return "".join([p.extract_text() for p in reader.pages])
    return ""

# --- UI ---
st.title("🤖 My AI Manager")
# DELETE THIS BLOCK
# --- DIAGNOSTIC TOOL ---
# if st.button("🔍 Check Available Models"):
# ...
# -----------------------

with st.sidebar:
    st.header("Upload Context")
    uploaded_file = st.file_uploader("Upload PDF/PNG", type=["pdf", "png"])
    st.divider()
    st.header("My Tasks")
    if st.button("Refresh Tasks"):
        st.rerun()
    for t in get_tasks():
        st.write(f"• {t}")

# Chat
if "messages" not in st.session_state: st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

user_input = st.chat_input("Type instruction (or use voice input on mobile)...")

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.write(user_input)

    # Prepare Context
    context = f"CURRENT TASKS: {get_tasks()}\n"
    if uploaded_file: context += f"FILE CONTENT: {read_file(uploaded_file)}\n"
    
    # Simple Router
    if "save" in user_input.lower() and "task" in user_input.lower():
        prompt = f"{context} User said: '{user_input}'. Extract the task content only."
        # Quick logic to save task
        add_task(user_input.replace("save task", "").strip())
        reply = "I have saved that task to your database."
    elif "search" in user_input.lower() or "news" in user_input.lower():
        search_data = web_search(user_input)
        prompt = f"Context: {context} \n Web Search: {search_data} \n User: {user_input} \n Answer:"
        response = model.generate_content(prompt)
        reply = response.text
    else:
        prompt = f"Context: {context} \n User: {user_input} \n Answer:"
        response = model.generate_content(prompt)
        reply = response.text

    # Reply
    with st.chat_message("assistant"):
        st.write(reply)
        audio_path = asyncio.run(speak(reply.replace("*", "")))
        st.audio(audio_path, autoplay=True)
    
    st.session_state.messages.append({"role": "assistant", "content": reply})
