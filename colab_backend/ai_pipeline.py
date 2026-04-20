#Cell 1: Install Dependencies & Mount Drive:
# ── Install all required packages on the Colab runtime ──
# ultralytics: YOLO object detection framework
# openai-clip: CLIP model for image-text embeddings
# faiss-gpu: GPU-accelerated vector similarity search
# langchain + langchain-community + langchain-openai: RAG pipeline
# crewai: Multi-agent orchestration framework
# pyngrok: ngrok tunnel to expose this Colab as a public API
# PyPDF2 + pypdf: PDF text extraction for the OSHA manual

import subprocess, sys

packages = [
    "ultralytics",
    "git+https://github.com/openai/CLIP.git",
    "langchain",
    "langchain-community",
    "langchain-openai",
    "langchain-text-splitters",
    "crewai",
    "pyngrok",
    "fastapi",
    "uvicorn",
    "python-multipart",
    "nest_asyncio",
    "pypdf",
    "PyPDF2",
    "httpx",
]

for pkg in packages:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

# --- Specific handling for FAISS ---
# Try to install faiss-gpu first. If it fails, fall back to faiss-cpu.
try:
    print("Attempting to install faiss-gpu...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "faiss-gpu"])
    print("faiss-gpu installed successfully.")
except subprocess.CalledProcessError as e:
    print(f"Warning: faiss-gpu installation failed with exit code {e.returncode}. Installing faiss-cpu instead.")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "faiss-cpu"])
    print("faiss-cpu installed successfully.")

print("All packages installed.")

from google.colab import drive, userdata
drive.mount("/content/drive")

API_SECRET = userdata.get("API_SECRET")
NEXUS_API_KEY = userdata.get("NEXUS_API_KEY")
NGROK_AUTH_TOKEN = userdata.get("NGROK_AUTH_TOKEN")

print(f"API Secret loaded: {'Yes' if API_SECRET else 'MISSING — add it in Secrets!'}")
print(f"Nexus API Key loaded: {'Yes' if NEXUS_API_KEY else 'MISSING'}")
print(f"ngrok token loaded: {'Yes' if NGROK_AUTH_TOKEN else 'MISSING'}")

#======================================================================================================================================================
#======================================================================================================================================================
#======================================================================================================================================================
# Cell 2: YOLO Detection
# Downloaded dataset on PPE violation from Roboflow with the classes `helmet`, `no-helmet`, `vest`, `no-vest`, `human`
# trained the dataset using a colab `trian.ipynb` script
# Uses the custom-trained `best.pt` model to detect PPE violations.
# Classes like `no-vest` and `no-helmet` are flagged as violations.

import os
import io
import base64
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from ultralytics import YOLO

# Path to your uploaded YOLO model on Drive 
MODEL_PATH = "/content/drive/MyDrive/SafetyScanAI/models/best.pt"

# Load the YOLO model
yolo_model = YOLO(MODEL_PATH)

#  class names count as violations 
VIOLATION_CLASSES = {"no-vest", "no-helmet", "No-Vest", "No-Helmet", "NO-VEST", "NO-HELMET"}

def detect_violations(image_bytes: bytes):
    """
    Run YOLO inference on raw image bytes.
    Returns:
      - violation_classes: list of detected violation labels (e.g. ["no-vest"])
      - annotated_b64: base64 string of the image with bounding boxes drawn
      - original_pil: the original PIL image (for downstream CLIP encoding)
    """
    # Convert raw bytes → PIL Image
    pil_image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Run YOLO detection
    results = yolo_model(pil_image, conf=0.25)

    violation_classes = []
    annotated = pil_image.copy()
    draw = ImageDraw.Draw(annotated)

    for result in results:
        for box in result.boxes:
            cls_id = int(box.cls[0])
            cls_name = yolo_model.names[cls_id]
            conf = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())

            # Check if this detection is a violation
            is_violation = cls_name.lower().replace("_", "-") in {v.lower() for v in VIOLATION_CLASSES}

            # Draw bounding box (red for violations, green for safe)
            color = (181, 51, 37) if is_violation else (52, 211, 153)
            draw.rectangle([x1, y1, x2, y2], outline=color, width=3)
            label = f"{cls_name} {conf:.0%}"
            draw.text((x1 + 4, y1 + 4), label, fill=color)

            if is_violation:
                violation_classes.append(cls_name.lower().replace("_", "-"))

    # Convert annotated image to base64 for sending to frontend
    buf = io.BytesIO()
    annotated.save(buf, format="JPEG", quality=85)
    annotated_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    # encode the original snapshot
    orig_buf = io.BytesIO()
    pil_image.save(orig_buf, format="JPEG", quality=85)
    snapshot_b64 = base64.b64encode(orig_buf.getvalue()).decode("utf-8")

    return {
        "violation_classes": list(set(violation_classes)),
        "annotated_image_base64": annotated_b64,
        "snapshot_base64": snapshot_b64,
        "original_pil": pil_image,
    }

print(f"YOLO model loaded from: {MODEL_PATH}")
print(f"Known classes: {list(yolo_model.names.values())}")

#======================================================================================================================================================
#======================================================================================================================================================
#======================================================================================================================================================
# ### Cell 3: CLIP + FAISS Image Similarity Search
# Compares the input image against the `/violations` library folder.
# If similarity > 0.15, consider it a verified breach.

import torch
import clip
import faiss

#  Load CLIP model for image embeddings 
device = "cuda" if torch.cuda.is_available() else "cpu"
clip_model, clip_preprocess = clip.load("ViT-B/32", device=device)

#  Path to violations library on Drive 
VIOLATIONS_FOLDER = "/content/drive/MyDrive/SafetyScanAI/violations"

# File names for the persistent FAISS index
INDEX_FILE = "/content/clip_violations.faiss"
PATHS_FILE = "/content/clip_violation_paths.npy"

def build_or_load_clip_index():
    """
    Build a FAISS index from all images in the violations folder.
    Saves to disk so subsequent runs are instant.
    """
    if os.path.exists(INDEX_FILE) and os.path.exists(PATHS_FILE):
        print("Loading existing CLIP index from disk...")
        index = faiss.read_index(INDEX_FILE)
        paths = np.load(PATHS_FILE).tolist()
        return index, paths

    print("Building CLIP index from violations folder (first time only)...")
    image_paths = [
        os.path.join(VIOLATIONS_FOLDER, f)
        for f in sorted(os.listdir(VIOLATIONS_FOLDER))
        if f.lower().endswith((".jpg", ".jpeg", ".png"))
    ]

    if not image_paths:
        raise ValueError(f"No images found in {VIOLATIONS_FOLDER}")

    # Batch-encode all violation images
    all_embeddings = []
    for i in range(0, len(image_paths), 16):
        batch = image_paths[i : i + 16]
        images = torch.stack([
            clip_preprocess(Image.open(p).convert("RGB")) for p in batch
        ]).to(device)
        with torch.no_grad():
            emb = clip_model.encode_image(images).cpu().numpy()
        all_embeddings.append(emb)

    embeddings = np.vstack(all_embeddings).astype("float32")
    faiss.normalize_L2(embeddings)

    # Build inner-product index (cosine similarity after normalization)
    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)

    # Save to disk
    faiss.write_index(index, INDEX_FILE)
    np.save(PATHS_FILE, np.array(image_paths))
    print(f"Saved index with {len(image_paths)} violation images.")

    return index, image_paths


def search_similar_violations(pil_image, index, image_paths, top_k=5, threshold=0.15):
    """
    Encode the input image with CLIP, search the FAISS index.
    Returns similarity score + base64-encoded similar images.
    """
    # Encode the query image
    img_tensor = clip_preprocess(pil_image).unsqueeze(0).to(device)
    with torch.no_grad():
        query_vec = clip_model.encode_image(img_tensor).cpu().numpy().astype("float32")
    faiss.normalize_L2(query_vec)

    # Search
    scores, indices = index.search(query_vec, k=top_k)

    # Collect results
    similar_images_b64 = []
    best_score = float(scores[0][0])

    for i, idx in enumerate(indices[0]):
        if idx < 0 or idx >= len(image_paths):
            continue
        # Convert each matched image to base64 for frontend display
        matched_img = Image.open(image_paths[idx]).convert("RGB")
        buf = io.BytesIO()
        matched_img.save(buf, format="JPEG", quality=70)
        similar_images_b64.append(base64.b64encode(buf.getvalue()).decode("utf-8"))

    return {
        "similarity_score": round(best_score, 4),
        "is_verified_breach": best_score > threshold,
        "similar_images": similar_images_b64,
    }


# Build the index on first run
clip_index, violation_paths = build_or_load_clip_index()
print(f"CLIP index ready — {len(violation_paths)} images indexed.")


#======================================================================================================================================================
#======================================================================================================================================================
#======================================================================================================================================================

# ### Cell 4: RAG Pipeline (OSHA Safety Manual)
# Retrieves specific legal clauses and penalties from the OSHA PDF based on the violation type detected.

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import FAISS as LangchainFAISS
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

#  Path to the OSHA safety manual PDF on Drive 
PDF_PATH = "/content/drive/MyDrive/SafetyScanAI/ragSource/safety_manual_for_construction_workers.pdf"
RAG_DB_PATH = "/content/rag_faiss_index"

#  Load and chunk the PDF 
print("Loading OSHA safety manual...")
loader = PyPDFLoader(PDF_PATH)
documents = loader.load()

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,       # Larger chunks to keep regulations intact
    chunk_overlap=100,
    separators=["\n\n", "\n", ". ", " "]
)
docs = text_splitter.split_documents(documents)
print(f"Split into {len(docs)} chunks.")

#  Create embeddings and vector store 
rag_embeddings = OpenAIEmbeddings(
    api_key=NEXUS_API_KEY,
    base_url="https://apidev.navigatelabsai.com",
    model="text-embedding-3-small"
)

if os.path.exists(RAG_DB_PATH):
    rag_vectorstore = LangchainFAISS.load_local(
        RAG_DB_PATH, rag_embeddings, allow_dangerous_deserialization=True
    )
    print("Loaded existing RAG index.")
else:
    rag_vectorstore = LangchainFAISS.from_documents(docs, rag_embeddings)
    rag_vectorstore.save_local(RAG_DB_PATH)
    print("Created and saved new RAG index.")

#  Build the retrieval chain 
rag_retriever = rag_vectorstore.as_retriever(search_kwargs={"k": 5})

rag_llm = ChatOpenAI(
    api_key=NEXUS_API_KEY,
    base_url="https://apidev.navigatelabsai.com",
    model="gpt-4.1-nano",
    temperature=0.3,
)

# Strict system prompt: answer ONLY from the OSHA manual context
rag_template = """You are a construction safety compliance expert.
Use ONLY the following OSHA safety manual context to extract details for a formal safety report. 
Keep the response strictly short, concise, and formatted for inclusion in a legal compliance email.

For the detected violation, extract and summarize:
- Legal Procedure: Specific OSHA regulations (e.g., 29 CFR) and clauses mandating this safety requirement.
- Required Actions: Concrete corrective measures and any mentioned industry standards (e.g., ANSI).
- Penalties: Any specific fines or legal implications mentioned in the manual.

If the answer is not in the context, say "Not covered in this manual."

CONTEXT: {context}
QUESTION: {question}
ANSWER:"""

rag_prompt = ChatPromptTemplate.from_template(rag_template)

# Compose the RAG chain: retrieve → prompt → LLM → parse
rag_chain = (
    {"context": rag_retriever, "question": RunnablePassthrough()}
    | rag_prompt
    | rag_llm
    | StrOutputParser()
)


def query_rag(violation_type: str) -> str:
    """
    Query the OSHA RAG engine for a specific violation type.
    Returns a text snippet with legal procedures and required actions.
    """
    query = (
        f"Extract the specific OSHA legal procedures, regulations, and required "
        f"corrective actions for a worker with {violation_type}. "
        f"Highlight specific CFR codes and industry standards."
    )
    return rag_chain.invoke(query)


print("RAG pipeline ready.")


#======================================================================================================================================================
#======================================================================================================================================================
#======================================================================================================================================================

# ### Cell 5: CrewAI Multi-Agent (Safety Auditor + Legal Critic)
# ***Agent 1*** generates an email-format compliance report.
# ***Agent 2*** reviews it for accuracy and legal tone, sends it back for revision if needed.

from crewai import LLM, Agent, Task, Crew

#  LLM configuration for both agents 
crew_llm = LLM(
    model="gpt-4.1-nano",
    temperature=0.7,
    base_url="https://apidev.navigatelabsai.com",
    api_key=NEXUS_API_KEY,
)

#  Agent 1: Safety Auditor 
# Generates the initial compliance email report from RAG data
safety_auditor = Agent(
    role="Safety Auditor",
    goal="Generate a professional email-format compliance report based on the safety violation data",
    backstory=(
        "You are a certified construction safety auditor with 15 years of experience. "
        "You write clear, actionable compliance reports that cite specific regulations. "
        "Your reports are formatted as professional emails ready to send to site managers."
    ),
    llm=crew_llm,
    verbose=True,
)

#  Agent 2: Legal Critic 
# Reviews the auditor's report for accuracy and proper legal tone
legal_critic = Agent(
    role="Legal Compliance Critic",
    goal="Review the safety report for technical accuracy, completeness, and proper legal tone",
    backstory=(
        "You are a workplace safety attorney who reviews compliance documents. "
        "You check that every cited regulation is accurate, the tone is professional, "
        "and no critical legal clauses are missing. If a report is incomplete, "
        "you send it back with specific revision requests."
    ),
    llm=crew_llm,
    verbose=True,
)


def run_agent_chain(violation_type: str, rag_output: str, admin_name: str, supervisor_name: str, sector_name: str) -> str:
    """
    Execute the 2-agent chain: Auditor drafts report → Critic reviews and finalizes.
    Returns the final polished email text including sector and supervisor context.
    """
    # Task 1: Draft the formal compliance email
    audit_task = Task(
        description=(
            f"Write a formal safety violation report email based on the following data:\n"
            f"VIOLATION TYPE: {violation_type}\n"
            f"OSHA REFERENCE DATA: {rag_output}\n"
            f"ADMIN/SENDER NAME: {admin_name}\n"
            f"SUPERVISOR NAME: {supervisor_name}\n"
            f"SECTOR/LOCATION: {sector_name}\n\n"
            f"The email MUST follow this EXACT structure:\n"
            f"Subject: SAFETY NOTICE: PPE Compliance Violation Report – [Current Date]\n\n"
            f"Greetings {supervisor_name},\n\n"
            f"This email serves as a formal report regarding a recent observation of non-compliance with our established Safety and Health protocols in {sector_name}.\n\n"
            f"Incident Overview\n"
            f"Date of Observation: {datetime.now().strftime('%d %B %Y')}\n"
            f"Time: {datetime.now().strftime('%H:%M')}\n"
            f"Location: {sector_name}\n\n"
            f"Violation Details\n"
            f"Type of Violation: {violation_type}\n"
            f"Legal Procedure: [Extract specific OSHA codes from the RAG data]\n\n"
            f"Corrective Actions Taken\n"
            f"[Summarize required actions and immediate interventions from RAG data]\n\n"
            f"Yours respectfully,\n"
            f"{admin_name}\n"
            f"Security Command Center"
        ),
        expected_output="A structured, formal safety report email addressed to the supervisor and signed by the admin.",
        agent=safety_auditor,
    )

    # Task 2: Review and finalize the report
    review_task = Task(
        description=(
            "Review the safety auditor's report for:\n"
            "1. Structural Accuracy: Does it follow the 'Incident Overview', 'Violation Details', 'Corrective Actions' format?\n"
            f"2. Salutation Check: It MUST be addressed specifically to 'Dear {supervisor_name},'.\n"
            f"3. Signature Check: It MUST end with 'Yours respectfully, {admin_name}, Security Command Center'.\n"
            "4. Tone: Ensure it is professional and legally sound.\n\n"
            "Output the FINAL polished email report."
        ),
        expected_output="A reviewed, finalized formal email report.",
        agent=legal_critic,
        context=[audit_task],
    )

    # Assemble and run the 2-agent crew
    crew = Crew(
        agents=[safety_auditor, legal_critic],
        tasks=[audit_task, review_task],
        verbose=True,
    )

    result = crew.kickoff()
    return str(result)


print("CrewAI agents ready (Safety Auditor + Legal Critic).")

#======================================================================================================================================================
#======================================================================================================================================================
#====================================================================================================================================================== 

#======================================================================================================================================================
# Cell 6: FastAPI Server (Triage Strategy) + ngrok/Localtunnel
# Tier 1: YOLO only (Fast)
# Tier 2: Search + RAG + Multi-Agent (Heavy) — Protected by Semaphore
#======================================================================================================================================================

import nest_asyncio
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, Header, Form
from fastapi.responses import JSONResponse
import uvicorn
from pyngrok import ngrok
import torch

# Allow asyncio to work inside Colab
nest_asyncio.apply()

# Authenticate ngrok
ngrok.set_auth_token(NGROK_AUTH_TOKEN)

# Create the Colab FastAPI app
colab_app = FastAPI(title="SafetyScanAI Triage Pipeline")

# CONCURRENCY CONTROL
# We use a semaphore to ensure only one Tier 2 (heavy) analysis runs at a time.
# This prevents memory crashes on Colab's T4 GPU.
tier2_semaphore = asyncio.Semaphore(1)
executor = ThreadPoolExecutor(max_workers=2)

@colab_app.get("/health")
async def colab_health():
    return {"status": "ok", "gpu": torch.cuda.is_available()}

@colab_app.get("/status")
async def get_status():
    """Returns whether the Tier 2 pipeline is currently busy."""
    return {"busy": tier2_semaphore.locked()}

@colab_app.post("/process-image")
async def process_image(
    file: UploadFile = File(...), 
    x_api_key: str = Header(None, alias="x-api-key"), 
    admin_name: str = Form("Admin"), 
    supervisor_name: str = Form("Supervisor"),
    supervisor_email: str = Form("unknown@example.com"),
    sector_id: str = Form("0"),
    sector_name: str = Form("Unknown Sector")
):
    """
    MAIN TRIAGE ENDPOINT
    Tier 1 (Fast Gate): YOLO only. If safe, return immediately.
    Tier 2 (Deep Analysis): Only on violations. Protected by Semaphore.
    """
    if x_api_key != API_SECRET:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Unauthorized"})

    try:
        image_bytes = await file.read()
        
        # --- TIER 1: FAST GATE (YOLO) ---
        # This part is relatively fast and runs for every frame.
        loop = asyncio.get_event_loop()
        detection = await loop.run_in_executor(executor, detect_violations, image_bytes)
        violations = detection["violation_classes"]

        if not violations:
            # Immediate return for safe frames
            return {
                "tier": 1,
                "status": "safe",
                "annotated_image_base64": detection["annotated_image_base64"],
                "message": "Safe"
            }

        # --- TIER 2: DEEP ANALYSIS (HEAVY) ---
        # If we reach here, a violation was found. We check the semaphore.
        if tier2_semaphore.locked():
            return {
                "tier": 1, 
                "status": "busy",
                "message": "Violation detected but system is busy with previous report. Skipping Tier 2."
            }

        async with tier2_semaphore:
            violation_label = ", ".join(violations)
            
            # Run heavy tasks in executor to keep the server responsive
            similarity = await loop.run_in_executor(
                executor, search_similar_violations, detection["original_pil"], clip_index, violation_paths
            )
            rag_output = await loop.run_in_executor(executor, query_rag, violation_label)
            agent_report = await loop.run_in_executor(
                executor, run_agent_chain, violation_label, rag_output, admin_name, supervisor_name, sector_name
            )

            result_data = {
                "violation_class": violation_label,
                "image_base64": detection["annotated_image_base64"],
                "image_filename": f"violation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg",
                "snapshot_base64": detection.get("snapshot_base64", ""),
                "similarity_score": similarity.get("similarity_score", 0),
                "is_verified_breach": similarity.get("is_verified_breach", False),
                "similar_images": similarity.get("similar_images", []),
                "rag_snippet": rag_output,
                "agent_report": agent_report,
                "admin_name": admin_name,
                "supervisor_name": supervisor_name,
                "supervisor_email": supervisor_email,
                "sector_id": sector_id,
                "sector_name": sector_name,
                "timestamp": datetime.now().isoformat(),
            }

            # Standardized Tier 2 Response
            return {
                "tier": 2,
                "status": "complete",
                "data": result_data
            }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})

# --- Server Startup Logic ---
import socket
import threading
import time
import subprocess
import re

def get_free_port(start_port=8001):
    """Finds an available port starting from start_port."""
    port = start_port
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('localhost', port)) != 0:
                print(f"Selected free port: {port}")
                return port
        port += 1

PORT = get_free_port()

def start_server():
    uvicorn.run(colab_app, host="0.0.0.0", port=PORT, log_level="error")

# 1. Start uvicorn on the free port
server_thread = threading.Thread(target=start_server, daemon=True)
server_thread.start()

time.sleep(3) # Wait for Uvicorn to initialize

print(f"\n{'='*60}")
print(f"  STARTING TUNNEL...")

#  OPTION A: Localtunnel 
print("Starting Localtunnel (npm install -g localtunnel -q)...")
try:
    subprocess.check_call("npm install -g localtunnel -q", shell=True)
except:
    pass

lt_process = subprocess.Popen(["lt", "--port", str(PORT)], stdout=subprocess.PIPE, text=True)
public_url = None
# non-blocking read
for line in lt_process.stdout:
    line_str = line.strip()
    if "your url is:" in line_str:
        public_url = line_str.replace("your url is: ", "")
        break

if not public_url:
    print("Localtunnel failed. Using Cloudflare TryTunnel fallback...")

    #  OPTION B: Cloudflared (Fallback) 
    subprocess.check_call("wget -q -nc https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O /usr/local/bin/cloudflared", shell=True)
    subprocess.check_call("chmod +x /usr/local/bin/cloudflared", shell=True)
    import os
    cf_process = subprocess.Popen(["cloudflared", "tunnel", "--url", f"http://localhost:{PORT}"], stderr=subprocess.PIPE)

    # wait a few seconds for the URL to appear 
    time.sleep(5)
    os.set_blocking(cf_process.stderr.fileno(), False)
    while not public_url:
        line = cf_process.stderr.readline()
        if not line: break
        line_str = line.decode('utf-8')
        match = re.search(r'(https://.*\.trycloudflare\.com)', line_str)
        if match:
            public_url = match.group(1)
            break

print(f"\n{'='*60}")
print(f"  COLAB API IS LIVE (EPHEMERAL MODE)")
print(f"  Public URL: {public_url}")
print(f"{'='*60}")

#======================================================================================================================================================
#======================================================================================================================================================
#====================================================================================================================================================== 