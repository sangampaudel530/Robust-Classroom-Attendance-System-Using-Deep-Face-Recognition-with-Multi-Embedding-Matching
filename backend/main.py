"""
main.py
FastAPI application entry point.
Run with: uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv

from backend.database import init_db
from backend.routers.attendance import get_video_processor, router as attendance_router
from backend.routers.students import router as students_router
from backend.routers.student_updates import router as student_updates_router
from backend.services.recognizer import EMBED_DIR, PHOTO_DIR, load_gallery
from backend.services.video_processor import AL_DIR, UPLOAD_DIR
import backend.models.attendance  # noqa: F401
import backend.models.student  # noqa: F401
import backend.models.active_learning  # noqa: F401
import backend.models.evaluation  # noqa: F401


load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".wmv", ".flv", ".m4v"}

def _cleanup_orphaned_videos():
    """Delete any leftover video files from data/uploads/ (from previous crashes)."""
    upload_dir = UPLOAD_DIR
    if not upload_dir.exists():
        return
    deleted = 0
    for f in upload_dir.iterdir():
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS:
            try:
                f.unlink()
                deleted += 1
            except OSError:
                pass
    if deleted:
        logger.info("Startup cleanup: removed %d orphaned video(s) from data/uploads/", deleted)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown events."""
    logger.info("Initializing database...")
    await init_db()

    # Ensure required data directories exist
    for directory in [PHOTO_DIR, EMBED_DIR, UPLOAD_DIR, AL_DIR]:
        directory.mkdir(parents=True, exist_ok=True)

    # Clean up orphaned video files left from previous runs (crashes, etc.)
    _cleanup_orphaned_videos()

    import onnxruntime as ort
    providers = ort.get_available_providers()
    has_gpu = "CUDAExecutionProvider" in providers
    logger.info("ONNX providers: %s | %s", providers, "GPU ENABLED" if has_gpu else "CPU ONLY (install onnxruntime-gpu for GPU)")

    # Pay the one-time InsightFace/GPU and gallery initialization cost before
    # the web UI becomes available. Video requests can then begin extracting
    # and processing frames immediately instead of pausing on "Starting".
    logger.info("Loading face-recognition engine...")
    await asyncio.to_thread(get_video_processor)
    await asyncio.to_thread(load_gallery)
    logger.info("Face-recognition engine ready.")

    logger.info("Face Attendance System started.")
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="Face Attendance System",
    description=(
        "AI-powered classroom attendance system using face detection, "
        "ArcFace recognition. "
        "Teachers enroll students, upload class photos, and get automatic P/A records."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Restrict to your domain in production
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(students_router,   prefix="/api")
app.include_router(student_updates_router, prefix="/api")
app.include_router(attendance_router, prefix="/api")


# Serve the frontend static files
FRONTEND_DIR = Path("frontend")
STATIC_DIR   = FRONTEND_DIR / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Expose only active-learning crops. Embeddings and class uploads must never be
# publicly downloadable through the static-file server.
app.mount(
    "/data/active_learning",
    StaticFiles(directory=str(AL_DIR)),
    name="active-learning-data",
)


@app.get("/", include_in_schema=False)
async def serve_frontend():
    """Serve the main teacher dashboard HTML."""
    index = FRONTEND_DIR / "templates" / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "Face Attendance API running. Visit /docs for API reference."}


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "Face Attendance System"}
