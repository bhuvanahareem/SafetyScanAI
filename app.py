#  SafetyScanAI — Cloud Command Center 
#  The bridge between your browser, Colab AI, and n8n automation.

import os
import asyncio
import json
import base64
import uuid
import logging
from datetime import datetime, timedelta
from typing import List, Optional

import httpx
from fastapi import FastAPI, UploadFile, File, WebSocket, WebSocketDisconnect, Request, Form, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("safetyscan")

# --- Configuration & Environment ---
COLAB_API_URL = os.getenv("COLAB_API_URL", "http://localhost:8001")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "")
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "default-demo-secret")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./safetyscan.db")

# Fix for Railway/Heroku postgres URLs
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# --- Database Setup ---
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Models ---
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    admin_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    sectors = relationship("Sector", back_populates="admin")

class Sector(Base):
    __tablename__ = "sectors"
    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, ForeignKey("users.id"))
    sector_name = Column(String(255), nullable=False)
    supervisor_name = Column(String(255), nullable=False)
    supervisor_email = Column(String(255), nullable=False)
    video_filename = Column(String(255), nullable=True)
    
    admin = relationship("User", back_populates="sectors")
    incidents = relationship("Incident", back_populates="sector", cascade="all, delete-orphan")

class Incident(Base):
    __tablename__ = "incidents"
    id = Column(Integer, primary_key=True, index=True)
    sector_id = Column(Integer, ForeignKey("sectors.id"))
    timestamp = Column(DateTime, default=datetime.utcnow)
    violation_type = Column(String(255), nullable=False)
    image_url = Column(Text, nullable=True)
    status = Column(String(50), default="Pending") # Pending, Closed
    
    sector = relationship("Sector", back_populates="incidents")

# Create tables
Base.metadata.create_all(bind=engine)

# --- Security ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, API_SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, API_SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user

# --- App Initialization ---
app = FastAPI(title="SafetyScanAI — Cloud Command Center")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure upload directories exist
os.makedirs("static/uploads/sectors", exist_ok=True)
os.makedirs("static/uploads/violations", exist_ok=True)

# --- WebSocket Connection Manager ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket disconnected. Remaining: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        logger.info(f"Broadcasting: {message.get('type', 'event')}")
        dead = []
        for conn in self.active_connections:
            try:
                await conn.send_json(message)
            except Exception:
                dead.append(conn)
        for d in dead:
            self.disconnect(d)

manager = ConnectionManager()

# --- Core API Routes ---

@app.post("/register")
async def register(admin_name: str = Form(...), email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    hashed_password = pwd_context.hash(password)
    new_user = User(admin_name=admin_name, email=email, password_hash=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    access_token = create_access_token(data={"sub": new_user.email})
    return {"status": "success", "access_token": access_token, "user_id": new_user.id, "admin_name": new_user.admin_name, "email": new_user.email}

@app.post("/login")
async def login(email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    if not user or not pwd_context.verify(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    
    access_token = create_access_token(data={"sub": user.email})
    return {"status": "success", "access_token": access_token, "user_id": user.id, "admin_name": user.admin_name, "email": user.email}

@app.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    return {"status": "success", "user_id": current_user.id, "admin_name": current_user.admin_name, "email": current_user.email}

@app.post("/logout")
async def logout():
    return {"status": "success"}

@app.put("/admin/profile")
async def update_profile(
    admin_name: str = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    current_user.admin_name = admin_name
    current_user.email = email
    db.commit()
    db.refresh(current_user)
    return {"status": "success", "admin_name": current_user.admin_name, "email": current_user.email}

@app.get("/sectors/{admin_id}")
async def get_sectors(admin_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if admin_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authorized")
    return db.query(Sector).filter(Sector.admin_id == admin_id).all()

@app.post("/upload-sector-video")
async def upload_sector_video(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    ext = os.path.splitext(file.filename)[1] or ".mp4"
    unique_name = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join("static", "uploads", "sectors", unique_name)
    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)
    return {"filename": unique_name, "url": f"/static/uploads/sectors/{unique_name}"}

@app.post("/setup-site")
async def setup_site(sectors_json: str = Form(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    sectors_data = json.loads(sectors_json)
    for s in sectors_data:
        new_sector = Sector(
            admin_id=current_user.id,
            sector_name=s['name'],
            supervisor_name=s['supervisor_name'],
            supervisor_email=s['supervisor_email'],
            video_filename=s.get('video_filename')
        )
        db.add(new_sector)
    db.commit()
    return {"status": "success"}

@app.post("/sectors/create")
async def create_sector(
    sector_name: str = Form(...),
    supervisor_name: str = Form(...),
    supervisor_email: str = Form(...),
    video_filename: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    new_sector = Sector(
        admin_id=current_user.id,
        sector_name=sector_name,
        supervisor_name=supervisor_name,
        supervisor_email=supervisor_email,
        video_filename=video_filename
    )
    db.add(new_sector)
    db.commit()
    db.refresh(new_sector)
    return new_sector

@app.get("/sectors/{sector_id}")
async def get_sector(sector_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    sector = db.query(Sector).filter(Sector.id == sector_id, Sector.admin_id == current_user.id).first()
    if not sector:
        raise HTTPException(status_code=404, detail="Sector not found")
    return sector

@app.put("/sectors/{sector_id}")
async def update_sector(
    sector_id: int,
    sector_name: str = Form(...),
    supervisor_name: str = Form(...),
    supervisor_email: str = Form(...),
    video_filename: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    sector = db.query(Sector).filter(Sector.id == sector_id, Sector.admin_id == current_user.id).first()
    if not sector:
        raise HTTPException(status_code=404, detail="Sector not found")
    
    sector.sector_name = sector_name
    sector.supervisor_name = supervisor_name
    sector.supervisor_email = supervisor_email
    if video_filename:
        sector.video_filename = video_filename
        
    db.commit()
    db.refresh(sector)
    return sector

@app.delete("/sectors/{sector_id}")
async def delete_sector(sector_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    sector = db.query(Sector).filter(Sector.id == sector_id, Sector.admin_id == current_user.id).first()
    if not sector:
        raise HTTPException(status_code=404, detail="Sector not found")
    
    db.delete(sector)
    db.commit()
    return {"status": "success"}

@app.get("/incidents")
async def get_incidents(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    results = db.query(Incident, Sector).join(Sector).filter(Sector.admin_id == current_user.id).order_by(Incident.timestamp.desc()).all()
    incidents = []
    for incident, sector in results:
        incidents.append({
            "id": incident.id,
            "sector_name": sector.sector_name,
            "timestamp": incident.timestamp.isoformat(),
            "violation_type": incident.violation_type,
            "image_url": incident.image_url,
            "status": incident.status
        })
    return incidents

@app.post("/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident.status = "Closed"
    db.commit()
    return {"status": "success"}

# --- AI Proxy & Callback Logic ---

@app.post("/process-frame")
async def process_frame(
    file: UploadFile = File(...), 
    sector_id: int = Form(...),
    admin_id: int = Form(...),
    db: Session = Depends(get_db)
):
    """
    Acts as a proxy: Browser -> Railway -> Colab Tunnel.
    Allows bypassing client-side network blocks.
    """
    image_bytes = await file.read()
    sector = db.query(Sector).filter(Sector.id == sector_id).first()
    admin = db.query(User).filter(User.id == admin_id).first()
    
    if not sector or not admin:
        return JSONResponse(status_code=400, content={"error": "Invalid sector or admin context"})

    try:
        base_url = COLAB_API_URL.rstrip("/")
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{base_url}/process-image",
                files={"file": (file.filename, image_bytes, "image/jpeg")},
                data={
                    "admin_name": admin.admin_name,
                    "supervisor_name": sector.supervisor_name,
                    "supervisor_email": sector.supervisor_email,
                    "sector_id": str(sector.id),
                    "sector_name": sector.sector_name
                },
                headers={"x-api-key": API_SECRET_KEY},
            )
            
        if response.status_code != 200:
            return JSONResponse(status_code=response.status_code, content=response.json())

        colab_result = response.json()
        
        # If Colab returns immediate results, process them
        if colab_result.get("status") == "complete":
            return await handle_detection(colab_result.get("data", {}), db)

        return colab_result

    except Exception as e:
        logger.error(f"Proxy failed: {e}")
        return JSONResponse(status_code=503, content={"error": "Colab backend unreachable via tunnel."})

@app.post("/detect")
async def detect_callback(request: Request, db: Session = Depends(get_db)):
    """
    Unified endpoint for Colab to push detection results (Async Callback).
    """
    payload = await request.json()
    # Check simple auth
    auth_key = request.headers.get("x-api-key")
    if auth_key != API_SECRET_KEY:
         raise HTTPException(status_code=401, detail="Unauthorized")

    return await handle_detection(payload, db)

async def handle_detection(violation_data: dict, db: Session):
    """
    Handles violation data: Save evidence -> Database -> WS Broadcast -> n8n.
    """
    sector_id = violation_data.get("sector_id")
    if not sector_id:
        return {"status": "ignored", "reason": "no_sector_id"}

    # Save evidence image if present
    image_b64 = violation_data.get("image_base64")
    image_web_url = None
    if image_b64:
        try:
            image_bytes = base64.b64decode(image_b64)
            img_filename = f"{uuid.uuid4().hex}.jpg"
            img_save_path = os.path.join("static", "uploads", "violations", img_filename)
            with open(img_save_path, "wb") as img_f:
                img_f.write(image_bytes)
            image_web_url = f"/static/uploads/violations/{img_filename}"
            # Keep base64 for live preview
            violation_data["uploaded_image_url"] = f"data:image/jpeg;base64,{image_b64}"
        except Exception as e:
            logger.error(f"Failed to save evidence: {e}")

    # Record in Database
    try:
        new_incident = Incident(
            sector_id=int(sector_id),
            violation_type=violation_data.get("violation_class", "Observation"),
            image_url=image_web_url,
            status="Pending"
        )
        db.add(new_incident)
        db.commit()
        violation_data["incident_id"] = new_incident.id
        violation_data["evidence_url"] = image_web_url
    except Exception as e:
        logger.error(f"Database error: {e}")

    # Broadcast to Frontend
    await manager.broadcast({"type": "violation", **violation_data})

    # Trigger n8n Automation
    if N8N_WEBHOOK_URL:
        asyncio.create_task(trigger_n8n(violation_data))

    return {"status": "processed", "incident_id": violation_data.get("incident_id")}

async def trigger_n8n(data: dict):
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(N8N_WEBHOOK_URL, json=data)
            logger.info(f"n8n triggered. Status: {resp.status_code}")
    except Exception as e:
        logger.error(f"n8n trigger failed: {e}")

# --- Frontend & Static Routes ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.get("/health")
async def health_check():
    return {"status": "online", "timestamp": datetime.utcnow().isoformat()}

# Mount static files *after* explicit API routes
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = os.path.join("static", "index.html")
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            return f.read()
    return "SafetyScanAI Command Center: Static assets not found."

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)