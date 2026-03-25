"""
Sessions Router — Endpoints for managing user sessions.
Developed by ChimSe (viduvan) - https://github.com/viduvan
"""
import logging

from fastapi import APIRouter, HTTPException

from ..models.schemas import SessionInfo, SessionListResponse
from ..core.session_manager import session_manager
from ..services.thumbnail_generator import cleanup_session_thumbnails

logger = logging.getLogger("odin_api.routers.sessions")
router = APIRouter(prefix="/api/sessions", tags=["Sessions"])


@router.get("", response_model=SessionListResponse)
async def list_sessions():
    """List all active sessions."""
    sessions = session_manager.list_sessions()
    return SessionListResponse(
        sessions=[
            SessionInfo(
                session_id=s.session_id,
                total_slides=len(s.slides),
                created_at=s.created_at,
                has_word_content=bool(s.word_content),
            )
            for s in sessions
        ],
        total=len(sessions),
    )


@router.get("/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str):
    """Get information about a specific session."""
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionInfo(
        session_id=session.session_id,
        total_slides=len(session.slides),
        created_at=session.created_at,
        has_word_content=bool(session.word_content),
    )


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and clean up its thumbnails."""
    deleted = session_manager.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")

    # Clean up session thumbnails
    removed = cleanup_session_thumbnails(session_id)
    logger.info(f"Deleted session '{session_id}', removed {removed} thumbnails")

    return {"message": "Session deleted successfully"}
