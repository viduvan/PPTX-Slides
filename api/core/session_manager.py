"""
In-memory session manager with TTL-based auto-cleanup.
For production, replace with Redis or database-backed storage.
"""
import uuid
import time
import threading
from datetime import datetime, timezone
from typing import Any

from .config import settings


class SessionData:
    """Holds all data for a single session."""

    def __init__(self):
        self.session_id: str = str(uuid.uuid4())
        self.slides: list[dict] = []
        self.slide_history: list[list[dict]] = []
        self.word_content: str = ""
        self.document_topic: str = ""
        self.template_name: str | None = None
        self.theme: str | None = None
        self.created_at: datetime = datetime.now(timezone.utc)
        self.last_accessed: float = time.time()

    def touch(self):
        """Update last accessed time."""
        self.last_accessed = time.time()


class SessionManager:
    """Manages slide generation sessions in memory."""

    def __init__(self, ttl_seconds: int | None = None):
        self._sessions: dict[str, SessionData] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds or settings.SESSION_TTL_SECONDS
        # Start background cleanup thread
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    # ── Public API ──────────────────────────────────────────

    def create_session(
        self,
        slides: list[dict],
        word_content: str = "",
        template_name: str | None = None,
    ) -> SessionData:
        """Create a new session with initial slides."""
        session = SessionData()
        session.slides = slides
        session.slide_history = [slides.copy()]
        session.word_content = word_content
        session.template_name = template_name
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> SessionData | None:
        """Get a session by ID, returns None if not found or expired."""
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            return None
        session.touch()
        return session

    def update_slides(self, session_id: str, slides: list[dict]) -> SessionData | None:
        """
Session Manager — Logic for handling user sessions and slide state persistence.
Developed by ChimSe (viduvan) - https://github.com/viduvan
"""
        session = self.get_session(session_id)
        if session is None:
            return None
        session.slides = slides
        session.slide_history.append(slides.copy())
        session.touch()
        return session

    def undo(self, session_id: str) -> SessionData | None:
        """Undo to previous slide version."""
        session = self.get_session(session_id)
        if session is None:
            return None
        if len(session.slide_history) > 1:
            session.slide_history.pop()
            session.slides = session.slide_history[-1].copy()
        session.touch()
        return session

    def list_sessions(self) -> list[SessionData]:
        """List all active sessions."""
        with self._lock:
            return list(self._sessions.values())

    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    # ── Background Cleanup ──────────────────────────────────

    def _cleanup_loop(self):
        """Periodically remove expired sessions."""
        while True:
            time.sleep(60)  # check every minute
            now = time.time()
            with self._lock:
                expired = [
                    sid for sid, s in self._sessions.items()
                    if now - s.last_accessed > self._ttl
                ]
                for sid in expired:
                    del self._sessions[sid]


# Singleton instance
session_manager = SessionManager()
