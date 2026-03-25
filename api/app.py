"""
FastAPI Application — Main entry point for the PPTX-Slides API.
Serves both the REST API and the frontend UI.

Developed by ChimSe (viduvan) - https://github.com/viduvan
Completed: February 27, 2026
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from .core.config import settings
from .routers import slides, upload, sessions

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("pptx_api")

# Frontend directory
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: setup and teardown."""
    import asyncio

    # Startup
    logger.info("PPTX-Slides API starting up...")
    logger.info(f"Temp directory: {settings.TEMP_DIR}")
    logger.info(f"Templates directory: {settings.TEMPLATES_DIR}")
    logger.info(f"Gemini model: {settings.GEMINI_MODEL}")
    logger.info(f"Frontend directory: {FRONTEND_DIR}")

    if not settings.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY is not set! API calls will fail.")

    # Pre-load templates in background (non-blocking)
    async def _preload():
        from .services.template_loader import preload_all_templates
        await asyncio.to_thread(preload_all_templates)

    asyncio.create_task(_preload())

    # Pre-generate slide thumbnails in background
    async def _generate_thumbs():
        from .services.thumbnail_generator import generate_all_thumbnails
        await asyncio.to_thread(generate_all_thumbnails)

    asyncio.create_task(_generate_thumbs())

    yield

    # Shutdown
    logger.info("PPTX-Slides API shutting down...")
    # Clean up temp PPTX files
    if settings.TEMP_DIR.exists():
        for f in settings.TEMP_DIR.glob("presentation_*.pptx"):
            try:
                f.unlink()
            except Exception:
                pass
        # Clean up temp PDF dirs from thumbnail generation
        import shutil
        for d in settings.TEMP_DIR.glob("thumb_*"):
            shutil.rmtree(d, ignore_errors=True)

    # Clean up ALL session thumbnails (not theme thumbnails)
    thumbnails_dir = Path(__file__).resolve().parent.parent / "assets" / "thumbnails"
    if thumbnails_dir.exists():
        count = 0
        for f in thumbnails_dir.glob("session_*.jpg"):
            try:
                f.unlink()
                count += 1
            except Exception:
                pass
        for f in thumbnails_dir.glob("session_*.png"):
            try:
                f.unlink()
                count += 1
            except Exception:
                pass
        if count:
            logger.info(f"Cleaned up {count} session thumbnails on shutdown")


# Create FastAPI app
app = FastAPI(
    title="PPTX-Slides API",
    description=(
        "REST API for generating PowerPoint presentations using Google Gemini AI. "
        "Upload Word documents, generate slides from prompts, edit interactively, "
        "preview in the browser, and download PPTX files."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware — allow all origins for development
# In production, restrict to your frontend domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
app.include_router(slides.router)
app.include_router(upload.router)
app.include_router(sessions.router)

# Serve slide thumbnails as static files
THUMBNAILS_DIR = Path(__file__).resolve().parent.parent / "assets" / "thumbnails"
THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/thumbnails", StaticFiles(directory=str(THUMBNAILS_DIR)), name="thumbnails")

# Serve frontend static files (CSS, JS)
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", response_class=HTMLResponse, tags=["Frontend"])
async def serve_frontend():
    """Serve the frontend UI (no-cache to ensure latest HTML is always delivered)."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(
            content=index_path.read_text(encoding="utf-8"),
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return HTMLResponse(
        content="<h1>PPTX-Slides API</h1><p>Frontend not found. Visit <a href='/docs'>/docs</a> for API docs.</p>"
    )


@app.get("/health", tags=["Health"])
async def health_check():
    """Detailed health check."""
    return {
        "status": "healthy",
        "gemini_key_set": bool(settings.GEMINI_API_KEY),
        "gemini_model": settings.GEMINI_MODEL,
        "temp_dir_exists": settings.TEMP_DIR.exists(),
        "templates_dir_exists": settings.TEMPLATES_DIR.exists(),
    }
