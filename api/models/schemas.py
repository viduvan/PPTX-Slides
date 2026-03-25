"""
Pydantic models for API request/response schemas.
"""
from datetime import datetime
from pydantic import BaseModel, Field


# ── Slide Data ──────────────────────────────────────────────

class SlideData(BaseModel):
    """Represents a single slide."""
    slide_number: float
    title: str
    content: str
    narration: str = ""
    image_keyword: str = ""


# ── Requests ────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    """Request body for generating slides."""
    prompt: str = Field(..., description="Prompt describing desired slides")
    word_content: str = Field("", description="Optional document content to base slides on")
    template_name: str | None = Field(None, description="Optional template file name")
    theme: str | None = Field(None, description="Theme preset name (e.g. 'ocean', 'midnight')")
    progress_id: str | None = Field(None, description="Client-generated ID for progress tracking")

class EditRequest(BaseModel):
    """Request body for editing existing slides."""
    session_id: str = Field(..., description="Session ID to edit")
    prompt: str = Field(..., description="Edit instruction")


class UndoRequest(BaseModel):
    """Request body for undo operation."""
    session_id: str


# ── Responses ───────────────────────────────────────────────

class GenerateResponse(BaseModel):
    """Response after generating or editing slides."""
    session_id: str
    slides: list[SlideData]
    message: str = "Slides generated successfully"


class PreviewResponse(BaseModel):
    """Response for slide preview."""
    session_id: str
    slides: list[SlideData]
    total_slides: int
    created_at: datetime


class SessionInfo(BaseModel):
    """Session metadata."""
    session_id: str
    total_slides: int
    created_at: datetime
    has_word_content: bool


class SessionListResponse(BaseModel):
    """Response listing all active sessions."""
    sessions: list[SessionInfo]
    total: int


class UploadResponse(BaseModel):
    """Response after uploading a Word document."""
    word_content: str
    word_count: int
    was_summarized: bool
    message: str = "Document processed successfully"


class ErrorResponse(BaseModel):
    """Standard error response."""
    detail: str
