"""
Application configuration.
Reads settings from environment variables or .env file.
Developed by ChimSe (viduvan) - https://github.com/viduvan
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from project root (if it exists)
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")


class Settings:
    """Application settings loaded from environment variables."""

    # Gemini API
    GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

    # File storage
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent
    TEMP_DIR: Path = BASE_DIR / "tmp"
    TEMPLATES_DIR: Path = BASE_DIR / "templates"

    # Session
    SESSION_TTL_SECONDS: int = int(os.environ.get("SESSION_TTL", "3600"))  # 1 hour

    # Pixabay API (optional, for slide images)
    PIXABAY_API_KEY: str = os.environ.get("PIXABAY_API_KEY", "")
    IMAGES_DIR: Path = BASE_DIR / "tmp" / "images"

    # Document processing
    MAX_WORD_COUNT_WITHOUT_SUMMARIZATION: int = 100000  # no summarization below this
    SUMMARIZE_CHUNK_SIZE: int = 20000                   # words per chunk for summarization
    MAX_CONTENT_FOR_LLM: int = 80000                    # safety limit: max words sent to LLM
    CHUNKED_SLIDE_THRESHOLD: int = 20000                # use chunked generation above this word count

    def __init__(self):
        self.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        self.TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        self.IMAGES_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
