"""
main.py
FastAPI application entry point.
Run with: uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
"""

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
from backend.routers.attendance import router as attendance_router
from backend.routers.students import router as students_router
import backend.models.attendance  # noqa: F401
import backend.models.student  # noqa: F401
import backend.models.active_learning  # noqa: F401


load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown events."""
    logger.info("Initializing database...")
    await init_db()

    # Ensure required data directories exist
    for d in ["data/student_photos", "data/embeddings", "data/uploads", "data/active_learning"]:
        Path(d).mkdir(parents=True, exist_ok=True)

    logger.info("Face Attendance System started.")
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="Face Attendance System",
    description=(
        "AI-powered classroom attendance system using face detection, "
        "ArcFace recognition, and anti-spoofing. "
        "Teachers enroll students, upload class photos, and get automatic P/A records."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Restrict to your domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(students_router,   prefix="/api")
app.include_router(attendance_router, prefix="/api")


# Serve the frontend static files
FRONTEND_DIR = Path("frontend")
STATIC_DIR   = FRONTEND_DIR / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Mount data directory to serve face crops for active learning
app.mount("/data", StaticFiles(directory="data"), name="data")


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