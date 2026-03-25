"""
Slides Router — Endpoints for generating, editing, previewing, and downloading slides.
Developed by ChimSe (viduvan) - https://github.com/viduvan
"""
import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..models.schemas import (
    GenerateRequest,
    GenerateResponse,
    EditRequest,
    PreviewResponse,
    SlideData,
    UndoRequest,
)
from ..services import llm_service, slide_service
from ..services.llm_service import get_generation_progress, clear_generation_progress
from ..services.template_builder import THEMES, AVAILABLE_THEMES, THEME_REGISTRY, THEME_CATEGORIES
from ..services.thumbnail_generator import (
    generate_thumbnails, get_thumbnail_paths,
    generate_session_thumbnails, get_session_thumbnail_paths, cleanup_session_thumbnails,
)
from ..core.session_manager import session_manager
from ..core.config import settings

logger = logging.getLogger("odin_api.routers.slides")
router = APIRouter(prefix="/api/slides", tags=["Slides"])


@router.get("/themes")
async def list_themes():
    """Return all available theme presets grouped by category."""
    categories = []
    for cat_id, cat_info in sorted(THEME_CATEGORIES.items(), key=lambda x: x[1]["order"]):
        cat_themes = []
        for theme_id in AVAILABLE_THEMES:
            reg = THEME_REGISTRY.get(theme_id)
            if not reg or reg["category"] != cat_id:
                continue
            colors = THEMES[theme_id]
            cat_themes.append({
                "id": theme_id,
                "label": reg["label"],
                "label_vi": reg.get("label_vi", reg["label"]),
                "emoji": reg["emoji"],
                "accent": "#{:02x}{:02x}{:02x}".format(*colors["accent"]),
                "bg": "#{:02x}{:02x}{:02x}".format(*colors["bg_gradient"]),
            })
        categories.append({
            "id": cat_id,
            "label": cat_info["label"],
            "label_vi": cat_info.get("label_vi", cat_info["label"]),
            "emoji": cat_info["emoji"],
            "themes": cat_themes,
        })
    return {"categories": categories, "default": "auto"}


@router.get("/themes/{theme_id}/preview")
async def preview_theme(theme_id: str):
    """Return theme metadata and real slide thumbnail URLs from PPTX files."""
    if theme_id not in THEMES:
        raise HTTPException(status_code=404, detail=f"Theme '{theme_id}' not found")

    reg = THEME_REGISTRY.get(theme_id, {})
    cat_id = reg.get("category", "")
    cat_info = THEME_CATEGORIES.get(cat_id, {})

    # Generate thumbnails on-demand if not cached
    thumbs = get_thumbnail_paths(theme_id)
    if not thumbs:
        thumbs = await asyncio.to_thread(generate_thumbnails, theme_id)

    slide_urls = []
    for i, p in enumerate(thumbs, start=1):
        slide_urls.append({
            "slide_number": i,
            "image_url": f"/thumbnails/{p.name}",
        })

    return {
        "theme_id": theme_id,
        "label": reg.get("label", theme_id),
        "label_vi": reg.get("label_vi", reg.get("label", theme_id)),
        "emoji": reg.get("emoji", "🎨"),
        "category": cat_info.get("label", cat_id),
        "category_vi": cat_info.get("label_vi", cat_id),
        "slides": slide_urls,
    }


@router.post("/generate", response_model=GenerateResponse)
async def generate_slides(request: GenerateRequest):
    """
    Generate a new set of slides from a prompt.
    Optionally provide word_content (from uploaded docx) to base the slides on.
    """
    import uuid
    progress_id = request.progress_id or str(uuid.uuid4())[:8]

    try:
        word_count = len(request.word_content.split()) if request.word_content else 0

        # Use chunked generation for large documents
        if word_count > settings.CHUNKED_SLIDE_THRESHOLD and request.word_content:
            logger.info(
                f"Large document ({word_count} words), using chunked slide generation"
            )
            result = await llm_service.generate_slides_chunked(
                prompt=request.prompt,
                word_content=request.word_content,
                session_id=progress_id,
            )
        else:
            result = await llm_service.generate_slides(
                prompt=request.prompt,
                word_content=request.word_content,
                existing_slides=[],
            )
        slides = result["slides"]
        document_topic = result.get("document_topic", "")
        # Use explicitly selected theme, or auto-detected
        theme = request.theme if request.theme and request.theme != "auto" else result.get("theme")

        # Create session
        template_path = slide_service.get_template_path(request.template_name)
        session = session_manager.create_session(
            slides=slides,
            word_content=request.word_content,
            template_name=str(template_path),
        )
        session.theme = theme
        session.document_topic = document_topic

        # Clean up progress tracking
        clear_generation_progress(progress_id)

        # Start thumbnail generation in background immediately
        # (gives a head start while frontend renders the response)
        _start_thumbnail_generation(session.session_id, session)

        return GenerateResponse(
            session_id=session.session_id,
            slides=[SlideData(**s) for s in slides],
            message=f"Generated {len(slides)} slides successfully",
        )

    except ValueError as e:
        clear_generation_progress(progress_id)
        error_msg = _get_user_friendly_error(str(e))
        raise HTTPException(status_code=400, detail=error_msg)
    except Exception as e:
        clear_generation_progress(progress_id)
        logger.error(f"Error generating slides: {e}")
        error_msg = _get_user_friendly_error(str(e))
        raise HTTPException(status_code=500, detail=error_msg)

@router.get("/progress/{progress_id}")
async def get_progress(progress_id: str):
    """Get real-time progress of slide generation (for chunked generation)."""
    progress = get_generation_progress(progress_id)
    if progress is None:
        return {"status": "unknown", "percent": 0, "message": ""}
    return progress


def _get_user_friendly_error(error_str: str) -> str:
    """Convert technical error messages to user-friendly Vietnamese messages."""
    e = error_str.lower()
    if "429" in e or "resource_exhausted" in e or "quota" in e:
        return "Đã hết lượt sử dụng AI trong ngày. Vui lòng thử lại vào ngày mai hoặc sử dụng API key khác."
    if "503" in e or "unavailable" in e or "timed out" in e:
        return "Hệ thống AI đang quá tải. Vui lòng thử lại sau vài phút."
    if "json" in e or "parse" in e or "expecting" in e:
        return "Không thể xử lý phản hồi từ AI. Vui lòng thử lại."
    if "no slides were generated" in e:
        return "Không thể tạo slide từ nội dung này. Vui lòng thử lại hoặc sử dụng file nhỏ hơn."
    return "Đã xảy ra lỗi khi tạo slide. Vui lòng thử lại sau."


@router.post("/edit", response_model=GenerateResponse)
async def edit_slides(request: EditRequest):
    """
    Edit existing slides using a prompt.
    The LLM will modify, add, or remove slides based on the instruction.
    """
    session = session_manager.get_session(request.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        # Get LLM response for edits
        result = await llm_service.generate_slides(
            prompt=request.prompt,
            word_content=session.word_content,
            existing_slides=session.slides.copy(),
        )
        new_slides = result["slides"]
        theme = result.get("theme")
        if theme:
            session.theme = theme

        # Merge new slides with existing
        merged = slide_service.merge_slides(
            existing_slides=session.slides.copy(),
            new_slides=new_slides,
        )

        # Update session
        session_manager.update_slides(request.session_id, merged)

        # Clear cached thumbnails so they regenerate for new slide content
        cleanup_session_thumbnails(request.session_id)

        return GenerateResponse(
            session_id=request.session_id,
            slides=[SlideData(**s) for s in merged],
            message=f"Slides updated. Now {len(merged)} slides total.",
        )

    except ValueError as e:
        error_msg = _get_user_friendly_error(str(e))
        raise HTTPException(status_code=400, detail=error_msg)
    except Exception as e:
        logger.error(f"Error editing slides: {e}")
        error_msg = _get_user_friendly_error(str(e))
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/{session_id}/preview", response_model=PreviewResponse)
async def preview_slides(session_id: str):
    """
    Get slide data for frontend preview rendering.
    Returns JSON representation of all slides.
    """
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    preview_data = slide_service.slides_to_preview(session.slides)
    return PreviewResponse(
        session_id=session_id,
        slides=[SlideData(**s) for s in preview_data],
        total_slides=len(preview_data),
        created_at=session.created_at,
    )


@router.get("/{session_id}/download")
async def download_slides(session_id: str):
    """
    Generate and download the PPTX file for the current session.
    """
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        template_path = session.template_name
        output_path = await slide_service.create_pptx(
            slides=session.slides,
            template_path=template_path,
            output_path=None,
            theme_name=session.theme,
            document_topic=session.document_topic,
        )

        return FileResponse(
            path=str(output_path),
            filename="presentation.pptx",
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )

    except Exception as e:
        logger.error(f"Error creating PPTX: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create PPTX file: {e}")


@router.put("/{session_id}/slides")
async def update_slides_directly(session_id: str, payload: dict):
    """
    Update slide data directly without calling LLM.
    Used for inline text editing in the frontend modal.
    """
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    slides = payload.get("slides", [])
    if not slides:
        raise HTTPException(status_code=400, detail="No slides provided")

    # Normalize slide data
    normalized = []
    for s in slides:
        normalized.append({
            "slide_number": int(s.get("slide_number", 0)),
            "title": s.get("title", ""),
            "content": s.get("content", ""),
            "narration": s.get("narration", ""),
            "image_keyword": s.get("image_keyword", ""),
        })

    session_manager.update_slides(session_id, normalized)
    cleanup_session_thumbnails(session_id)

    return {
        "session_id": session_id,
        "total_slides": len(normalized),
        "message": "Slides updated successfully",
    }


@router.get("/{session_id}/thumbnails")
async def get_slide_thumbnails(session_id: str):
    """
    Return thumbnail URLs for session slides (progressive).
    Returns whatever thumbnails exist so far + status:
    - 'ready': all thumbnails generated
    - 'generating': still rendering, poll again for more
    """
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    total_slides = len(session.slides)

    # Return whatever thumbnails exist right now
    existing = get_session_thumbnail_paths(session_id)
    slides_data = [
        {"slide_number": i, "image_url": f"/thumbnails/{p.name}"}
        for i, p in enumerate(existing, start=1)
    ]

    is_generating = session_id in _generating_sessions
    all_done = len(existing) >= total_slides

    if not existing and not is_generating:
        # No thumbnails and not generating — start background generation
        _start_thumbnail_generation(session_id, session)
        is_generating = True

    return {
        "session_id": session_id,
        "status": "ready" if all_done else "generating",
        "total": total_slides,
        "slides": slides_data,
    }


# Background thumbnail generation tracking
_generating_sessions: set[str] = set()


async def _generate_thumbnails_bg(session_id: str, session):
    """Background coroutine to generate thumbnails."""
    try:
        template_path = session.template_name
        output_path = await slide_service.create_pptx(
            slides=session.slides,
            template_path=template_path,
            output_path=None,
            theme_name=session.theme,
            document_topic=session.document_topic,
        )
        await asyncio.to_thread(
            generate_session_thumbnails, session_id, output_path
        )
        logger.info(f"Background thumbnail generation complete for '{session_id}'")
    except Exception as e:
        logger.error(f"Background thumbnail generation failed for '{session_id}': {e}")
    finally:
        _generating_sessions.discard(session_id)


def _start_thumbnail_generation(session_id: str, session):
    """Start background thumbnail generation if not already running."""
    if session_id in _generating_sessions:
        return
    _generating_sessions.add(session_id)
    asyncio.create_task(_generate_thumbnails_bg(session_id, session))


@router.post("/{session_id}/undo", response_model=GenerateResponse)
async def undo_slides(session_id: str):
    """
    Undo the last edit and revert to the previous version.
    """
    session = session_manager.undo(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Clear cached thumbnails so they regenerate
    cleanup_session_thumbnails(session_id)

    return GenerateResponse(
        session_id=session_id,
        slides=[SlideData(**s) for s in session.slides],
        message="Reverted to previous version",
    )
