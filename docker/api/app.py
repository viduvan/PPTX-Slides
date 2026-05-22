"""
API Gateway — Thin proxy between Frontend and n8n Pipeline.

This service:
1. Receives requests from the frontend (upload docs, generate slides)
2. Triggers n8n webhooks to start the multi-agent pipeline
3. Polls job status from Postgres (pptx_app.jobs table)
4. Serves generated files (PPTX, HTML) from shared volume
5. Serves the frontend UI as static files

NO LLM calls, NO slide generation logic. All intelligence is in n8n workflows.

Developed by ChimSe (viduvan) - https://github.com/viduvan
"""
import logging
import os
import uuid
from pathlib import Path

import aiohttp
import asyncpg
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api_gateway")

# ── Config ───────────────────────────────────────────────────
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "http://n8n:5678/webhook")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://pptx:pptx@postgres:5432/pptx_slides")
SHARED_DATA_DIR = Path(os.getenv("SHARED_DATA_DIR", "/data"))
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", "/app/frontend"))

# ── App ──────────────────────────────────────────────────────
app = FastAPI(
    title="PPTX-Slides API Gateway",
    description="API Gateway for the multi-agent PPTX presentation pipeline",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# DB connection pool
_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _pool


@app.on_event("startup")
async def startup():
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        logger.info("Database connection established")
    except Exception as e:
        logger.warning(f"Database not available yet: {e}")


@app.on_event("shutdown")
async def shutdown():
    global _pool
    if _pool:
        await _pool.close()


# ── Request/Response Models ──────────────────────────────────

class GenerateRequest(BaseModel):
    prompt: str = Field(..., description="Prompt describing desired slides")
    word_content: str = Field("", description="Document content to base slides on")
    theme: str = Field("auto", description="Theme preset name")
    output_format: str = Field("pptx", description="Output format: pptx, html, both")


class GenerateResponse(BaseModel):
    job_id: str
    status: str
    message: str


class JobStatus(BaseModel):
    job_id: str
    status: str
    progress_pct: int = 0
    pptx_path: str = ""
    html_path: str = ""
    thumbnail_paths: list[str] = []
    error: str = ""
    slide_count: int = 0


class UploadResponse(BaseModel):
    document_text: str
    word_count: int
    message: str


# ── Frontend Serving ─────────────────────────────────────────

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/", response_class=HTMLResponse, tags=["Frontend"])
async def serve_frontend():
    """Serve the frontend UI."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(
            content=index_path.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return HTMLResponse(
        content="<h1>PPTX-Slides API</h1>"
        "<p>Frontend not found. Visit <a href='/docs'>/docs</a> for API docs.</p>"
    )


# ── Core Endpoints ───────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health():
    pool_ok = False
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        pool_ok = True
    except Exception:
        pass

    return {
        "status": "healthy" if pool_ok else "degraded",
        "database": pool_ok,
        "n8n_url": N8N_WEBHOOK_URL,
        "shared_data": SHARED_DATA_DIR.exists(),
    }


@app.post("/api/upload/document", response_model=UploadResponse, tags=["Upload"])
async def upload_document(file: UploadFile = File(...)):
    """
    Upload a document (.docx or .pdf).
    Extracts text and returns it for the generate endpoint.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in {".docx", ".pdf", ".txt"}:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Supported: .docx, .pdf, .txt"
        )

    # Save to shared volume for processing
    upload_dir = SHARED_DATA_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_id = str(uuid.uuid4())
    upload_path = upload_dir / f"{upload_id}_{file.filename}"

    content = await file.read()
    upload_path.write_bytes(content)

    # Extract text based on file type
    document_text = ""
    if ext == ".txt":
        document_text = content.decode("utf-8", errors="replace")
    elif ext == ".docx":
        document_text = _extract_docx_text(upload_path)
    elif ext == ".pdf":
        document_text = _extract_pdf_text(upload_path)

    word_count = len(document_text.split())

    logger.info(f"Uploaded '{file.filename}': {word_count} words")
    return UploadResponse(
        document_text=document_text,
        word_count=word_count,
        message=f"Document processed: {word_count} words extracted",
    )


@app.post("/api/slides/generate", response_model=GenerateResponse, tags=["Slides"])
async def generate_slides(req: GenerateRequest):
    """
    Trigger the multi-agent pipeline via n8n webhook.
    Returns a job_id for status polling.
    """
    job_id = str(uuid.uuid4())

    # Create job record in DB
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pptx_app.jobs (job_id, status, progress_pct, prompt, output_format)
                VALUES ($1, 'queued', 0, $2, $3)
                """,
                job_id, req.prompt[:500], req.output_format,
            )
    except Exception as e:
        logger.error(f"DB insert failed: {e}")
        # Continue even if DB fails — n8n will create its own tracking

    # Trigger n8n webhook asynchronously
    payload = {
        "job_id": job_id,
        "prompt": req.prompt,
        "document_text": req.word_content,
        "theme": req.theme,
        "output_format": req.output_format,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{N8N_WEBHOOK_URL}/pptx-generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    logger.error(f"n8n webhook failed ({resp.status}): {body}")
                    raise HTTPException(
                        status_code=502,
                        detail="Pipeline service unavailable. Please try again."
                    )
    except aiohttp.ClientError as e:
        logger.error(f"n8n connection error: {e}")
        raise HTTPException(
            status_code=502,
            detail="Pipeline service unavailable. Please try again."
        )

    return GenerateResponse(
        job_id=job_id,
        status="queued",
        message="Pipeline started. Use /api/jobs/{job_id} to track progress.",
    )


@app.get("/api/jobs/{job_id}", response_model=JobStatus, tags=["Jobs"])
async def get_job_status(job_id: str):
    """
    Poll the status of a generation job.
    Frontend calls this every 2-3 seconds until status is 'done' or 'error'.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT job_id, status, progress_pct, pptx_path, html_path,
                       thumbnail_paths, error_message, slide_count
                FROM pptx_app.jobs WHERE job_id = $1
                """,
                job_id,
            )
    except Exception as e:
        logger.error(f"DB query failed: {e}")
        raise HTTPException(status_code=500, detail="Database unavailable")

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    thumb_paths = []
    if row["thumbnail_paths"]:
        import json
        try:
            thumb_paths = json.loads(row["thumbnail_paths"])
        except (json.JSONDecodeError, TypeError):
            pass

    return JobStatus(
        job_id=row["job_id"],
        status=row["status"],
        progress_pct=row["progress_pct"] or 0,
        pptx_path=row["pptx_path"] or "",
        html_path=row["html_path"] or "",
        thumbnail_paths=thumb_paths,
        error=row["error_message"] or "",
        slide_count=row["slide_count"] or 0,
    )


@app.get("/api/download/{job_id}/{format}", tags=["Download"])
async def download_file(job_id: str, format: str):
    """
    Download generated file (pptx or html).
    """
    if format not in ("pptx", "html"):
        raise HTTPException(status_code=400, detail="Format must be 'pptx' or 'html'")

    # Find file path from DB
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            col = "pptx_path" if format == "pptx" else "html_path"
            path = await conn.fetchval(
                f"SELECT {col} FROM pptx_app.jobs WHERE job_id = $1",
                job_id,
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Database unavailable")

    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail=f"File not found for job {job_id}")

    file_path = Path(path)
    media_type = (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        if format == "pptx" else "text/html"
    )
    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type=media_type,
    )


@app.get("/api/thumbnails/{job_id}/{slide_num}", tags=["Thumbnails"])
async def get_thumbnail(job_id: str, slide_num: int):
    """Serve a slide thumbnail image."""
    thumb_dir = SHARED_DATA_DIR / "thumbnails" / job_id
    thumb_path = thumb_dir / f"slide_{slide_num}.png"
    if not thumb_path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(path=str(thumb_path), media_type="image/png")


@app.get("/api/themes", tags=["Themes"])
async def list_themes():
    """
    List available HTML themes (from html-assets).
    Reads directly from the shared html-assets volume.
    """
    themes_dir = SHARED_DATA_DIR.parent / "html-assets" / "themes"  # fallback
    # Try reading from exporter service
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "http://exporter:8004/themes",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception:
        pass

    # Fallback: scan local themes dir
    html_assets = Path("/app/html-assets/themes")
    if html_assets.exists():
        themes = sorted([f.stem for f in html_assets.glob("*.css")])
        return {"themes": themes, "count": len(themes)}

    return {"themes": [], "count": 0}


@app.get("/api/layouts", tags=["Layouts"])
async def list_layouts():
    """List available HTML slide layouts."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "http://exporter:8004/layouts",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception:
        pass
    return {"layouts": [], "count": 0}


# ── Text Extraction Helpers ──────────────────────────────────

def _extract_docx_text(file_path: Path) -> str:
    """Extract text from .docx using python-docx (or zipfile fallback)."""
    try:
        import zipfile
        import xml.etree.ElementTree as ET

        text_parts = []
        with zipfile.ZipFile(file_path) as zf:
            with zf.open("word/document.xml") as doc:
                tree = ET.parse(doc)
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                for p in tree.iter(f"{{{ns['w']}}}p"):
                    para_text = []
                    for t in p.iter(f"{{{ns['w']}}}t"):
                        if t.text:
                            para_text.append(t.text)
                    if para_text:
                        text_parts.append("".join(para_text))
        return "\n".join(text_parts)
    except Exception as e:
        logger.error(f"DOCX extraction failed: {e}")
        return ""


def _extract_pdf_text(file_path: Path) -> str:
    """Extract text from PDF using pdfplumber (if available) or basic extraction."""
    try:
        import subprocess
        result = subprocess.run(
            ["pdftotext", "-layout", str(file_path), "-"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass

    # Fallback: try to read as text
    try:
        return file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
