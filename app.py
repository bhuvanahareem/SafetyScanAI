# LOCAL FASTAPI SERVER — The bridge between your browser and Colab.
#   1. Serves the frontend HTML/CSS/JS
#   2. Accepts image uploads from the browser
#   3. Forwards images to the Colab AI pipeline (via ngrok tunnel)
#   4. Pushes real-time violation events to the browser via WebSocket
#   5. Triggers n8n webhook to send compliance emails

import os
import asyncio
import json
import base64
import uuid
from datetime import datetime
from typing import List, Optional

import httpx
from fastapi import FastAPI, UploadFile, File, WebSocket, WebSocketDisconnect, Request, Form, Depends, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from passlib.context import CryptContext
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship

from config import COLAB_API_URL, N8N_WEBHOOK_URL, API_SECRET_KEY, DATABASE_URL

#  Database Setup 
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

#  Models 
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
    incidents = relationship("Incident", back_populates="sector")

class Incident(Base):
    __tablename__ = "incidents"
    id = Column(Integer, primary_key=True, index=True)
    sector_id = Column(Integer, ForeignKey("sectors.id"))
    timestamp = Column(DateTime, default=datetime.now)
    violation_type = Column(String(255), nullable=False)
    image_url = Column(Text, nullable=True)
    status = Column(String(50), default="Pending") # Pending, Closed
    
    sector = relationship("Sector", back_populates="incidents")

# Create tables
Base.metadata.create_all(bind=engine)

#  Security 
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

#  App initialization 
app = FastAPI(title="SafetyScanAI — Multi-Sector Command Center")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("static/uploads/sectors", exist_ok=True)

os.makedirs("static/uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.post("/register")
async def register(admin_name: str = Form(...), email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    # Check if user exists
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    hashed_password = pwd_context.hash(password)
    new_user = User(admin_name=admin_name, email=email, password_hash=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"status": "success", "user_id": new_user.id, "admin_name": new_user.admin_name}

@app.post("/login")
async def login(email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    if not user or not pwd_context.verify(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    
    return {"status": "success", "user_id": user.id, "admin_name": user.admin_name}

@app.post("/setup-site")
async def setup_site(
    admin_id: int = Form(...),
    sectors_json: str = Form(...), # JSON string containing list of sector data
    db: Session = Depends(get_db)
):
    """
    Receives metadata for multiple sectors. 
    Frontend will handle the concurrent file uploads per sector separately if needed, 
    but for simplicity here, we assume metadata first.
    """
    sectors_data = json.loads(sectors_json)
    created_sectors = []
    
    for s in sectors_data:
        new_sector = Sector(
            admin_id=admin_id,
            sector_name=s['name'],
            supervisor_name=s['supervisor_name'],
            supervisor_email=s['supervisor_email'],
            video_filename=s.get('video_filename')
        )
        db.add(new_sector)
        created_sectors.append(new_sector)
    
    db.commit()
    return {"status": "success", "count": len(created_sectors)}

@app.post("/upload-sector-video")
async def upload_sector_video(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename)[1] or ".mp4"
    unique_name = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join("static", "uploads", "sectors", unique_name)
    
    with open(save_path, "wb") as f:
        f.write(await file.read())
        
    return {"filename": unique_name, "url": f"/static/uploads/sectors/{unique_name}"}

@app.get("/sectors/{admin_id}")
async def get_sectors(admin_id: int, db: Session = Depends(get_db)):
    sectors = db.query(Sector).filter(Sector.admin_id == admin_id).all()
    return sectors

@app.get("/incidents")
async def get_incidents(db: Session = Depends(get_db)):
    # Join with Sector to get sector_name
    results = db.query(Incident, Sector).join(Sector).order_by(Incident.timestamp.desc()).all()
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
async def resolve_incident(incident_id: int, db: Session = Depends(get_db)):
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident.status = "Closed"
    db.commit()
    return {"status": "success"}


#  WebSocket Connection Manager 
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead = []
        for conn in self.active_connections:
            try:
                await conn.send_json(message)
            except Exception:
                dead.append(conn)
        for d in dead:
            if d in self.active_connections:
                self.active_connections.remove(d)

manager = ConnectionManager()


#  Routes 

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()


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


@app.post("/upload-image")
async def upload_image(
    file: UploadFile = File(...), 
    user_name: str = Form(None), 
    user_email: str = Form(None),
    sector_id: int = Form(None),
    admin_id: int = Form(None),
    db: Session = Depends(get_db)
):
    ext = os.path.splitext(file.filename)[1] or ".jpg"
    unique_name = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join("static", "uploads", unique_name)
    
    image_bytes = await file.read()
    with open(save_path, "wb") as f:
        f.write(image_bytes)

    try:
        base_url = COLAB_API_URL.rstrip("/")
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{base_url}/process-image",
                files={"file": (unique_name, image_bytes, file.content_type or "image/jpeg")},
                data={
                    "user_name": user_name, 
                    "user_email": user_email,
                    "sector_id": sector_id
                },
                headers={"x-api-key": API_SECRET_KEY},
            )
            
        if response.status_code != 200:
            return JSONResponse(status_code=response.status_code, content=response.json())

        colab_result = response.json()

        # Handle Tier 2 (Deep Analysis) results
        if colab_result.get("status") == "complete":
            return await _handle_processed_violation(colab_result, sector_id, admin_id, db)

        return colab_result

    except (httpx.ConnectError, httpx.TimeoutException):
        return JSONResponse(status_code=503, content={"error": "Colab backend unreachable."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Internal Server Error: {str(e)}"})


@app.post("/process-frame")
async def process_frame(
    file: UploadFile = File(...), 
    sector_id: int = Form(...),
    admin_id: int = Form(...),
    db: Session = Depends(get_db)
):
    """
    Handles snapshots from the video monitoring loop.
    """
    image_bytes = await file.read()
    
    # Fetch sector/admin info
    sector = db.query(Sector).filter(Sector.id == sector_id).first()
    admin = db.query(User).filter(User.id == admin_id).first()
    
    if not sector or not admin:
        return JSONResponse(status_code=400, content={"error": "Invalid sector or admin ID"})

    try:
        base_url = COLAB_API_URL.rstrip("/")
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                f"{base_url}/process-image",
                files={"file": (file.filename, image_bytes, file.content_type or "image/jpeg")},
                data={
                    "admin_name": admin.admin_name,
                    "supervisor_name": sector.supervisor_name,
                    "supervisor_email": sector.supervisor_email,
                    "sector_id": sector.id,
                    "sector_name": sector.sector_name
                },
                headers={"x-api-key": API_SECRET_KEY},
            )
            
        if response.status_code != 200:
            return JSONResponse(status_code=response.status_code, content=response.json())

        colab_result = response.json()
        
        if colab_result.get("status") == "complete":
            return await _handle_processed_violation(colab_result, sector_id, admin_id, db)

        return colab_result

    except (httpx.ConnectError, httpx.TimeoutException):
        return JSONResponse(status_code=503, content={"error": "Colab backend unreachable."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Internal Server Error: {str(e)}"})



@app.get("/colab-status")
async def get_colab_status():
    """Proxies the busy/idle status from Colab."""
    try:
        base_url = COLAB_API_URL.rstrip("/")
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{base_url}/status")
            return response.json()
    except Exception:
        return {"busy": False, "connected": False}


async def _handle_processed_violation(colab_result: dict, sector_id: Optional[int], admin_id: Optional[int], db: Session):
    """
    Helper to broadcast violation data to WebSockets and trigger n8n email.
    Now also records the incident in PostgreSQL.
    """
    violation_data = colab_result.get("data", {})
    
    # Use the 'image_base64' from Colab (annotated with boxes) for the dashboard
    if violation_data.get("image_base64"):
        violation_data["uploaded_image_url"] = f"data:image/jpeg;base64,{violation_data.get('image_base64')}"
    
    # Enrich with sector context if available
    if sector_id and db:
        sector = db.query(Sector).filter(Sector.id == sector_id).first()
        if sector:
            violation_data["sector_id"] = sector.id
            violation_data["sector_name"] = sector.sector_name
            violation_data["supervisor_name"] = sector.supervisor_name
            violation_data["supervisor_email"] = sector.supervisor_email
            
    if admin_id and db:
        admin = db.query(User).filter(User.id == admin_id).first()
        if admin:
            violation_data["admin_name"] = admin.admin_name

    # Record in Database
    if sector_id:
        new_incident = Incident(
            sector_id=sector_id,
            violation_type=violation_data.get("violation_class", "Unknown"),
            image_url=violation_data.get("uploaded_image_url"),
            status="Pending"
        )
        db.add(new_incident)
        db.commit()
        violation_data["incident_id"] = new_incident.id

    # Push to live dashboard
    await manager.broadcast({"type": "violation", **violation_data})

    # Trigger email only if a violation report was actually generated
    if violation_data.get("agent_report"):
        await _trigger_n8n(violation_data)

    return {"status": "complete", "data": violation_data}


async def _trigger_n8n(violation_data: dict):
    """
    Sends the annotated image and enriched report to n8n.
    """
    try:
        payload = {
            "violation_class": violation_data.get("violation_class", "unknown"),
            "agent_report": violation_data.get("agent_report", ""),
            "rag_snippet": violation_data.get("rag_snippet", ""),
            "image_base64": violation_data.get("image_base64", ""),
            "image_filename": violation_data.get("image_filename", "violation.jpg"),
            "timestamp": violation_data.get("timestamp", datetime.now().isoformat()),
            "sector_id": violation_data.get("sector_id"),
            "sector_name": violation_data.get("sector_name", "Unknown Sector"),
            "supervisor_name": violation_data.get("supervisor_name", "Supervisor"),
            "supervisor_email": violation_data.get("supervisor_email", "unknown@example.com"),
            "admin_name": violation_data.get("admin_name", "Admin"),
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(N8N_WEBHOOK_URL, json=payload)
            print(f"[n8n] Triggered for sector {payload['sector_id']}. Status: {resp.status_code}")
    except Exception as e:
        print(f"[n8n] Webhook trigger failed: {e}")


@app.post("/violation-event")
async def violation_event(request: Request):
    payload = await request.json()
    await manager.broadcast({"type": "violation", **payload})
    return {"status": "broadcasted"}


@app.post("/n8n-callback")
async def n8n_callback(request: Request):
    # N8N hits this endpoint when the email successfully sends.
    payload = await request.json()
    await manager.broadcast({
        "type": "email_sent",
        "user_name": payload.get("user_name", "User"),
        "timestamp": datetime.now().isoformat()
    })
    return {"status": "acknowledged"}


@app.get("/health")
async def health_check():
    colab_ok = False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{COLAB_API_URL}/health")
            colab_ok = r.status_code == 200
    except Exception:
        pass

    return {
        "local_server": True,
        "colab_connected": colab_ok,
        "websocket_clients": len(manager.active_connections),
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)