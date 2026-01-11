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

# --- CONFIGURATION ---
st.set_page_config(page_title="My AI Second Brain", layout="wide")

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
    st.warning("⚠️ Database disconnected. Memory won't work.")
    db = None

# 2. Setup Brain (Gemini)
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.error("Missing Gemini API Key.")
    st.stop()

# USE THE STABLE ALIAS (High Quota)
model_name = 'gemini-flash-latest'
model = genai.GenerativeModel(model_name)

# --- DATABASE FUNCTIONS ---
def get_tasks():
    """Fetch pending tasks"""
    if not db: return []
    try:
        docs = db.collection('tasks').stream()
        return [f"{doc.id}: {doc.to_dict().get('task')}" for doc in docs]
    except: return []

def add_task(task_text):
    """Save a task"""
    if db: db.collection('tasks').add({'task': task_text, 'status': 'pending'})

def get_memories():
    """Fetch Long-Term Memories (RAG)"""
    if not db: return []
    try:
        docs = db.collection('memories').stream()
        return [doc.to_dict().get('fact') for doc in docs]
    except: return []

def add_memory(fact_text):
    """Save a permanent fact"""
    if db: db.collection('memories').add({
        'fact': fact_text, 
        'timestamp': firestore.SERVER_TIMESTAMP
    })

# --- HELPER FUNCTIONS ---
def web_search(query):
    try:
        results = DDGS().text(query, max_results=3)
        return str(results)
    except: return "No internet results found."

async def speak(text):
    communicate = edge_tts.Communicate(text, "en-IN-NeerjaNeural")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
        await communicate.save(fp.name)
        return fp.name

def process_file(uploaded):
    """
    Universal File Handler:
    - PDF -> Returns Text
    - XML/TXT/JSON -> Returns Text
    - PNG/JPG -> Returns Image Object (For Gemini Vision)
    """
    try:
        # Handle Images
        if uploaded.type in ["image/png", "image/jpeg", "image/jpg"]:
            return Image.open(uploaded)
        
        # Handle PDF
        elif uploaded.type == "application/pdf":
            reader = PyPDF2.PdfReader(uploaded)
            return "".join([p.extract_text() for p in reader.pages])
            
        # Handle Text/Code/XML
        else:
            return uploaded.getvalue().decode("utf-8")
    except Exception as e:
        return f"Error reading file: {str(e)}"

def ask_gemini(prompt_content):
    """
    CRASH PROTECTION: 
    Catches 429 (Rate Limit) and 404 (Not Found) errors.
    Accepts either a String (text only) or List (multimodal).
    """
    try:
        response = model.generate_content(prompt_content)
        return response.text
    except Exception as e:
        err_msg = str(e)
        if "429" in err_msg or "ResourceExhausted" in err_msg:
            return "⚠️ Speed limit reached. Please wait 20 seconds."
        elif "NotFound" in err_msg:
            return f"Error: Model {model_name} not found."
        else:
            return f"System Error: {err_msg}"

# --- UI INTERFACE ---
st.title("🧠 My AI Second Brain")

with st.sidebar:
    st.header("Upload Context")
    uploaded_file = st.file_uploader("Upload File", type=["pdf", "png", "jpg", "xml", "txt", "json"])
    
    st.divider()
    st.header("📝 Pending Tasks")
    if st.button("Refresh Data"): st.rerun()
    
    tasks = get_tasks()
    if tasks:
        for t in tasks: st.write(f"• {t}")
    else: st.write("No tasks.")

    st.divider()
    st.header("🧠 Long-Term Memories")
    memories = get_memories()
    if memories:
        for m in memories: st.write(f"🔹 {m}")
    else: st.write("I don't know anything about you yet.")

# Chat History
if "messages" not in st.session_state: st.session_state.messages = []
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]): st.write(msg["content"])

# User Input
user_input = st.chat_input("Type instruction (or use voice)...")

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"): st.write(user_input)

    # 1. Process File
    file_data = None
    if uploaded_file:
        file_data = process_file(uploaded_file)
    
    # 2. Build RAG Context (Text Only)
    # We create the "Base Prompt" that includes your memories and tasks.
    text_context = f"""
    SYSTEM: You are a personal assistant.
    USER MEMORIES (Facts about user): {memories}
    CURRENT PENDING TASKS: {tasks}
    """
    
    # 3. Router Logic
    reply = ""
    lower_input = user_input.lower()
    
    # CASE A: Save Memory
    if "remember that" in lower_input or "remember i" in lower_input:
        fact = user_input.replace("remember that", "").replace("remember", "").strip()
        add_memory(fact)
        reply = f"🧠 Memory Stored: {fact}"

    # CASE B: Save Task
    elif "save task" in lower_input:
        clean_task = user_input.replace("save task", "").strip()
        add_task(clean_task)
        reply = f"✅ Task Saved: {clean_task}"

    # CASE C: Search
    elif "search" in lower_input or "news" in lower_input:
        st.status("Searching the web...", expanded=False)
        web_res = web_search(user_input)
        # Combine everything into a text prompt
        full_prompt = f"{text_context} \n WEB RESULTS: {web_res} \n QUESTION: {user_input}"
        reply = ask_gemini(full_prompt)

    # CASE D: General Chat (Handles Images/Files)
    else:
        # If we have an image, we must send a LIST [text, image]
        # If we have text file, we append it to the string.
        
        prompt_payload = []
        
        if isinstance(file_data, Image.Image):
            # It's an image. Send [Context + Question, Image]
            prompt_payload = [f"{text_context} \n QUESTION: {user_input}", file_data]
        elif isinstance(file_data, str):
            # It's a text file (PDF/XML). Add to string.
            prompt_payload = [f"{text_context} \n FILE CONTENT: {file_data} \n QUESTION: {user_input}"]
        else:
            # No file. Just text.
            prompt_payload = [f"{text_context} \n QUESTION: {user_input}"]

        reply = ask_gemini(prompt_payload)

    # 4. Output
    with st.chat_message("assistant"):
        st.write(reply)
        if "⚠️" not in reply:
            audio_path = asyncio.run(speak(reply.replace("*", "")))
            st.audio(audio_path, autoplay=True)
    
    st.session_state.messages.append({"role": "assistant", "content": reply})
