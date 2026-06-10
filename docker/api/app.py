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
                timeout=aiohttp.ClientTimeout(total=30),
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
                       thumbnail_paths, error_message, slide_count, exporter_result
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
            thumb_paths = json.loads(row["thumbnail_paths"]) if isinstance(row["thumbnail_paths"], str) else row["thumbnail_paths"]
        except (json.JSONDecodeError, TypeError):
            pass

    slide_count = row["slide_count"] or 0
    # Fallback to parsing exporter_result or thumbnail_paths if slide_count is 0
    if not slide_count:
        if thumb_paths:
            slide_count = len(thumb_paths)
        elif row["exporter_result"]:
            import json
            try:
                exp_res = row["exporter_result"]
                if isinstance(exp_res, str):
                    exp_res = json.loads(exp_res)
                if exp_res and "file_paths" in exp_res:
                    file_paths = exp_res["file_paths"]
                    if isinstance(file_paths, dict):
                        if "thumbnail_paths" in file_paths:
                            slide_count = len(file_paths["thumbnail_paths"])
                        elif "thumbnails" in file_paths:
                            slide_count = len(file_paths["thumbnails"])
            except Exception as e:
                logger.warning(f"Failed to extract slide_count from exporter_result: {e}")

    return JobStatus(
        job_id=row["job_id"],
        status=row["status"],
        progress_pct=row["progress_pct"] or 0,
        pptx_path=row["pptx_path"] or "",
        html_path=row["html_path"] or "",
        thumbnail_paths=thumb_paths,
        error=row["error_message"] or "",
        slide_count=slide_count,
    )


@app.get("/api/download/{job_id}/{format}", tags=["Download"])
async def download_file(job_id: str, format: str):
    """
    Download generated file (pptx, html, or pdf).
    """
    if format not in ("pptx", "html", "pdf"):
        raise HTTPException(status_code=400, detail="Format must be 'pptx', 'html', or 'pdf'")

    # Find file path from DB
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            if format == "pptx":
                path = await conn.fetchval(
                    "SELECT pptx_path FROM pptx_app.jobs WHERE job_id = $1",
                    job_id,
                )
            elif format == "html":
                path = await conn.fetchval(
                    "SELECT html_path FROM pptx_app.jobs WHERE job_id = $1",
                    job_id,
                )
            else:  # pdf
                pptx_path = await conn.fetchval(
                    "SELECT pptx_path FROM pptx_app.jobs WHERE job_id = $1",
                    job_id,
                )
                if not pptx_path:
                    raise HTTPException(status_code=404, detail=f"Job {job_id} not found or has no PPTX")
                path = str(Path(pptx_path).with_suffix(".pdf"))
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail="Database unavailable")

    if format == "pdf" and not Path(path).exists():
        # Call exporter to convert pptx to pdf
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(
                    "http://exporter:8004/export-pdf",
                    json={"job_id": job_id, "pptx_path": pptx_path},
                    timeout=aiohttp.ClientTimeout(total=130),
                ) as resp:
                    if resp.status != 200:
                        err_text = await resp.text()
                        logger.error(f"Exporter PDF conversion failed: {err_text}")
                        raise HTTPException(status_code=500, detail="Failed to convert PPTX to PDF via Exporter")
                    resp_data = await resp.json()
                    path = resp_data["pdf_path"]
            except Exception as e:
                logger.error(f"Failed to communicate with exporter for PDF conversion: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to convert PPTX to PDF: {str(e)}")

    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail=f"File not found for job {job_id}")

    file_path = Path(path)
    if format == "pptx":
        media_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    elif format == "html":
        media_type = "text/html"
    else:  # pdf
        media_type = "application/pdf"

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type=media_type,
    )


@app.get("/api/preview/{job_id}/html", tags=["Preview"])
async def preview_html(job_id: str):
    """
    Serve the HTML presentation for live preview.
    """
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            path = await conn.fetchval(
                "SELECT html_path FROM pptx_app.jobs WHERE job_id = $1",
                job_id,
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Database unavailable")

    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail=f"HTML presentation not found for job {job_id}")

    return FileResponse(path=path, media_type="text/html")


@app.get("/api/preview/{job_id}/assets/{path:path}", tags=["Preview"])
async def preview_assets(job_id: str, path: str):
    """
    Serve HTML presentation assets (CSS, JS, fonts).
    """
    # Sanitize path to prevent directory traversal
    base_assets_dir = (SHARED_DATA_DIR / "output" / job_id / "assets").resolve()
    
    # Resolve the requested asset path
    asset_file = (base_assets_dir / path).resolve()
    
    # Check if the resolved path is within the base directory
    if not str(asset_file).startswith(str(base_assets_dir)):
        raise HTTPException(status_code=403, detail="Access denied")
        
    if not asset_file.exists() or not asset_file.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
        
    # Auto-detect media type
    media_type = None
    if asset_file.suffix == ".css":
        media_type = "text/css"
    elif asset_file.suffix == ".js":
        media_type = "application/javascript"
    elif asset_file.suffix in (".woff", ".woff2"):
        media_type = "font/woff2" if asset_file.suffix == ".woff2" else "font/woff"
    elif asset_file.suffix in (".png", ".jpg", ".jpeg"):
        media_type = f"image/{asset_file.suffix[1:]}"
        
    return FileResponse(path=str(asset_file), media_type=media_type)


@app.api_route("/api/thumbnails/{job_id}/{slide_num}", methods=["GET", "HEAD"], tags=["Thumbnails"])
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
    List available HTML themes grouped by category.
    """
    categories_def = {
        "business":    {"label": "Business",    "label_vi": "Doanh nghiệp",    "emoji": "🏢", "order": 1},
        "creative":    {"label": "Creative",    "label_vi": "Sáng tạo",        "emoji": "🎨", "order": 2},
        "education":   {"label": "Education",   "label_vi": "Giáo dục",        "emoji": "📚", "order": 3},
        "technology":  {"label": "Technology",  "label_vi": "Công nghệ",       "emoji": "💻", "order": 4},
    }

    theme_registry = {
        "corporate_blue":  {"category": "business",   "label": "Corporate Blue",  "label_vi": "Xanh doanh nghiệp",  "emoji": "💼", "accent": "#1d4ed8", "bg": "#10203c"},
        "executive_gray":  {"category": "business",   "label": "Executive Gray",  "label_vi": "Xám sang trọng",     "emoji": "🏛️", "accent": "#b8960c", "bg": "#1e1e22"},
        "finance_green":   {"category": "business",   "label": "Finance Green",   "label_vi": "Xanh tài chính",     "emoji": "📊", "accent": "#059609", "bg": "#0a201a"},
        "legal_navy":      {"category": "business",   "label": "Professional Desk", "label_vi": "Bàn làm việc",      "emoji": "💼", "accent": "#4338ca", "bg": "#0e142c"},
        "consulting_teal": {"category": "business",   "label": "Geometric Bold",   "label_vi": "Hình học nổi bật",   "emoji": "🔷", "accent": "#0d9488", "bg": "#0b2e2a"},
        
        "bold_orange":     {"category": "creative",   "label": "Watercolor Nature", "label_vi": "Thiên nhiên màu nước", "emoji": "🎣", "accent": "#ea580c", "bg": "#2d1606"},
        "artistic_purple": {"category": "creative",   "label": "Notebook Grid",    "label_vi": "Sổ tay kẻ ô",         "emoji": "📓", "accent": "#7c3aed", "bg": "#1f1035"},
        "neon_pop":        {"category": "creative",   "label": "Eco Green",        "label_vi": "Xanh sinh thái",      "emoji": "🌿", "accent": "#059669", "bg": "#062016"},
        "retro_vintage":   {"category": "creative",   "label": "Workspace Desk",   "label_vi": "Không gian làm việc",  "emoji": "🖥️", "accent": "#b45309", "bg": "#271c10"},
        "rose_pink":       {"category": "creative",   "label": "Watercolor Earth", "label_vi": "Trái đất màu nước",   "emoji": "🌍", "accent": "#db2777", "bg": "#2d0f1e"},
        
        "scholar_blue":    {"category": "education",  "label": "Classroom Board",  "label_vi": "Bảng lớp học",       "emoji": "📋", "accent": "#2563eb", "bg": "#0f1e36"},
        "campus_green":    {"category": "education",  "label": "Open Book",        "label_vi": "Sách mở",            "emoji": "📖", "accent": "#16a34a", "bg": "#0b2414"},
        "library_brown":   {"category": "education",  "label": "Graduation Idea",  "label_vi": "Ý tưởng tốt nghiệp", "emoji": "🎓", "accent": "#92400e", "bg": "#22140d"},
        "science_teal":    {"category": "education",  "label": "Creative Pencil",  "label_vi": "Bút chì sáng tạo",   "emoji": "✏️", "accent": "#0d9488", "bg": "#092422"},
        "chalkboard":      {"category": "education",  "label": "Chalkboard",       "label_vi": "Bảng phấn",          "emoji": "📝", "accent": "#4b5563", "bg": "#1f2937"},
        
        "cyber_punk":      {"category": "technology", "label": "Network Connect",  "label_vi": "Mạng kết nối",       "emoji": "🔗", "accent": "#d946ef", "bg": "#1e0b36"},
        "matrix_green":    {"category": "technology", "label": "Cyber Vision",     "label_vi": "Tầm nhìn số",        "emoji": "👁️", "accent": "#22c55e", "bg": "#022c22"},
        "ai_blue":         {"category": "technology", "label": "AI Blue",          "label_vi": "Xanh AI",            "emoji": "🧠", "accent": "#3b82f6", "bg": "#031e45"},
        "quantum_violet":  {"category": "technology", "label": "Space Rocket",     "label_vi": "Tên lửa vũ trụ",     "emoji": "🚀", "accent": "#8b5cf6", "bg": "#1e0b36"},
        "data_orange":     {"category": "technology", "label": "Robot Hand",       "label_vi": "Tay robot",          "emoji": "🤖", "accent": "#f97316", "bg": "#2c1402"},
    }

    categories = []
    for cat_id, cat_info in sorted(categories_def.items(), key=lambda x: x[1]["order"]):
        cat_themes = []
        for theme_id, reg in theme_registry.items():
            if reg["category"] != cat_id:
                continue
            cat_themes.append({
                "id": theme_id,
                "label": reg["label"],
                "label_vi": reg["label_vi"],
                "emoji": reg["emoji"],
                "accent": reg["accent"],
                "bg": reg["bg"]
            })
        categories.append({
            "id": cat_id,
            "label": cat_info["label"],
            "label_vi": cat_info["label_vi"],
            "emoji": cat_info["emoji"],
            "themes": cat_themes
        })

    return {"categories": categories, "default": "corporate_blue"}


@app.get("/api/slides/themes", tags=["Themes"])
async def list_slides_themes():
    """Alias for compatibility with frontend code."""
    return await list_themes()


@app.get("/api/themes/{theme_id}/preview", tags=["Themes"])
async def get_theme_preview(theme_id: str):
    """
    Return theme metadata and preview slide thumbnails.
    Generates inline SVG slide thumbnails encoded in Base64 for offline compatibility.
    """
    import base64
    
    theme_registry = {
        "corporate_blue":  {"category": "business",   "label": "Corporate Blue",  "label_vi": "Xanh doanh nghiệp",  "emoji": "💼", "category_label": "Business", "category_label_vi": "Doanh nghiệp"},
        "executive_gray":  {"category": "business",   "label": "Executive Gray",  "label_vi": "Xám sang trọng",     "emoji": "🏛️", "category_label": "Business", "category_label_vi": "Doanh nghiệp"},
        "finance_green":   {"category": "business",   "label": "Finance Green",   "label_vi": "Xanh tài chính",     "emoji": "📊", "category_label": "Business", "category_label_vi": "Doanh nghiệp"},
        "legal_navy":      {"category": "business",   "label": "Professional Desk", "label_vi": "Bàn làm việc",      "emoji": "💼", "category_label": "Business", "category_label_vi": "Doanh nghiệp"},
        "consulting_teal": {"category": "business",   "label": "Geometric Bold",   "label_vi": "Hình học nổi bật",   "emoji": "🔷", "category_label": "Business", "category_label_vi": "Doanh nghiệp"},
        
        "bold_orange":     {"category": "creative",   "label": "Watercolor Nature", "label_vi": "Thiên nhiên màu nước", "emoji": "🎣", "category_label": "Creative", "category_label_vi": "Sáng tạo"},
        "artistic_purple": {"category": "creative",   "label": "Notebook Grid",    "label_vi": "Sổ tay kẻ ô",         "emoji": "📓", "category_label": "Creative", "category_label_vi": "Sáng tạo"},
        "neon_pop":        {"category": "creative",   "label": "Eco Green",        "label_vi": "Xanh sinh thái",      "emoji": "🌿", "category_label": "Creative", "category_label_vi": "Sáng tạo"},
        "retro_vintage":   {"category": "creative",   "label": "Workspace Desk",   "label_vi": "Không gian làm việc",  "emoji": "🖥️", "category_label": "Creative", "category_label_vi": "Sáng tạo"},
        "rose_pink":       {"category": "creative",   "label": "Watercolor Earth", "label_vi": "Trái đất màu nước",   "emoji": "🌍", "category_label": "Creative", "category_label_vi": "Sáng tạo"},
        
        "scholar_blue":    {"category": "education",  "label": "Classroom Board",  "label_vi": "Bảng lớp học",       "emoji": "📋", "category_label": "Education", "category_label_vi": "Giáo dục"},
        "campus_green":    {"category": "education",  "label": "Open Book",        "label_vi": "Sách mở",            "emoji": "📖", "category_label": "Education", "category_label_vi": "Giáo dục"},
        "library_brown":   {"category": "education",  "label": "Graduation Idea",  "label_vi": "Ý tưởng tốt nghiệp", "emoji": "🎓", "category_label": "Education", "category_label_vi": "Giáo dục"},
        "science_teal":    {"category": "education",  "label": "Creative Pencil",  "label_vi": "Bút chì sáng tạo",   "emoji": "✏️", "category_label": "Education", "category_label_vi": "Giáo dục"},
        "chalkboard":      {"category": "education",  "label": "Chalkboard",       "label_vi": "Bảng phấn",          "emoji": "📝", "category_label": "Education", "category_label_vi": "Giáo dục"},
        
        "cyber_punk":      {"category": "technology", "label": "Network Connect",  "label_vi": "Mạng kết nối",       "emoji": "🔗", "category_label": "Technology", "category_label_vi": "Công nghệ"},
        "matrix_green":    {"category": "technology", "label": "Cyber Vision",     "label_vi": "Tầm nhìn số",        "emoji": "👁️", "category_label": "Technology", "category_label_vi": "Công nghệ"},
        "ai_blue":         {"category": "technology", "label": "AI Blue",          "label_vi": "Xanh AI",            "emoji": "🧠", "category_label": "Technology", "category_label_vi": "Công nghệ"},
        "quantum_violet":  {"category": "technology", "label": "Space Rocket",     "label_vi": "Tên lửa vũ trụ",     "emoji": "🚀", "category_label": "Technology", "category_label_vi": "Công nghệ"},
        "data_orange":     {"category": "technology", "label": "Robot Hand",       "label_vi": "Tay robot",          "emoji": "🤖", "category_label": "Technology", "category_label_vi": "Công nghệ"},
    }
    
    reg = theme_registry.get(
        theme_id, {"label": theme_id.title(), "label_vi": theme_id.title(), "emoji": "🎨", "category_label": "Modern", "category_label_vi": "Hiện đại"}
    )
    
    # Generate 3 beautiful mock SVG slides in base64 format
    slides = []
    for i in range(1, 4):
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450" viewBox="0 0 800 450">
            <rect width="800" height="450" fill="#1e1e2e" rx="10"/>
            <circle cx="400" cy="225" r="150" fill="#313244" opacity="0.5"/>
            <rect x="50" y="50" width="700" height="350" fill="none" stroke="#45475a" stroke-width="2" rx="5"/>
            <text x="400" y="200" fill="#cdd6f4" font-family="system-ui, sans-serif" font-size="32" font-weight="bold" text-anchor="middle">
                {theme_id.upper()}
            </text>
            <text x="400" y="260" fill="#a6adc8" font-family="system-ui, sans-serif" font-size="20" text-anchor="middle">
                Slide {i} Preview
            </text>
            <rect x="350" y="300" width="100" height="4" fill="#f38ba8" rx="2"/>
        </svg>"""
        b64_svg = base64.b64encode(svg.encode('utf-8')).decode('utf-8')
        image_url = f"data:image/svg+xml;base64,{b64_svg}"
        slides.append({
            "slide_number": i,
            "image_url": image_url
        })
        
    return {
        "theme_id": theme_id,
        "label": reg["label"],
        "label_vi": reg["label_vi"],
        "emoji": reg["emoji"],
        "category": reg["category_label"],
        "category_vi": reg["category_label_vi"],
        "slides": slides,
    }


@app.get("/api/slides/themes/{theme_id}/preview", tags=["Themes"])
async def get_slides_theme_preview(theme_id: str):
    """Alias for compatibility with frontend code."""
    return await get_theme_preview(theme_id)


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
