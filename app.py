
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

import httpx
from fastapi import FastAPI, UploadFile, File, WebSocket, WebSocketDisconnect, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from config import COLAB_API_URL, N8N_WEBHOOK_URL, API_SECRET_KEY

#  App initialization 
app = FastAPI(title="SafetyScanAI — Safety Monitoring Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("static/uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


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
async def upload_image(file: UploadFile = File(...), user_name: str = Form(None), user_email: str = Form(None)):
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
                data={"user_name": user_name, "user_email": user_email},
                headers={"x-api-key": API_SECRET_KEY},
            )
            
        if response.status_code != 200:
            return JSONResponse(status_code=response.status_code, content=response.json())

        colab_result = response.json()

        if colab_result.get("status") == "complete":
            violation_data = colab_result.get("data", {})
            
            # Use the 'image_base64' from Colab (annotated with boxes) for the dashboard
            violation_data["uploaded_image_url"] = f"data:image/jpeg;base64,{violation_data.get('image_base64')}"
            
            await manager.broadcast({"type": "violation", **violation_data})

            # Trigger email only if a violation report was actually generated
            if violation_data.get("agent_report"):
                await _trigger_n8n(violation_data)

            return {"status": "complete", "data": violation_data}

        return colab_result

    except Exception as e:
        print(f"DEBUG Error: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


async def _trigger_n8n(violation_data: dict):
    """
    Sends the annotated image and report to n8n.
    Uses the base64 string provided by Colab to ensure bounding boxes are present.
    """
    try:
        payload = {
            "violation_class": violation_data.get("violation_class", "unknown"),
            "agent_report": violation_data.get("agent_report", ""),
            "rag_snippet": violation_data.get("rag_snippet", ""),
            "image_base64": violation_data.get("image_base64", ""),
            "image_filename": violation_data.get("image_filename", "violation.jpg"),
            "timestamp": violation_data.get("timestamp", datetime.now().isoformat()),
            "user_name": violation_data.get("user_name", "Unknown User"),
            "user_email": violation_data.get("user_email", "unknown@example.com"),
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(N8N_WEBHOOK_URL, json=payload)
            print(f"[n8n] Triggered. Status Code: {resp.status_code}")
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
    await manager.broadcast({"type": "email_sent"})
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