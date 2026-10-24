"""FastAPI Server for Aura Skincare Voice Agent (Aria).

Provides /health, static web files, and real-time WebSocket communication.
"""

import json
import logging
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from app.session import CallSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("app.main")

app = FastAPI(title="Aura Skincare AI Voice Agent - Aria")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/health")
async def health_check():
    """Health check endpoint for container orchestrators and monitoring."""
    return {"status": "ok", "agent": "Aria", "brand": "Aura Skincare"}


@app.websocket("/ws/call")
async def websocket_call_endpoint(websocket: WebSocket):
    """Real-time voice and control WebSocket endpoint."""
    await websocket.accept()
    session = CallSession(ws=websocket)
    logger.info("WebSocket connected.")

    try:
        while True:
            msg = await websocket.receive()
            if "text" in msg:
                try:
                    data = json.loads(msg["text"])
                except Exception:
                    continue

                msg_type = data.get("type")
                if msg_type == "start_call":
                    await session.start_call()
                elif msg_type == "user_text":
                    await session.handle_user_text(data.get("text", ""))
                elif msg_type == "set_user":
                    session.set_active_customer(data.get("customer", ""))
                elif msg_type == "playback_done":
                    session.start_silence_watcher()
                elif msg_type == "barge_in":
                    await session.handle_barge_in()
                elif msg_type == "end_call":
                    await session.end_call()

            elif "bytes" in msg:
                await session.handle_audio_bytes(msg["bytes"])

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        if session.is_active:
            await session.end_call()


# Mount static folder and serve index.html at root
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse({"status": "Aura Skincare - Aria Ready"})
