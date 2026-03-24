"""
Document Service — Logic for reading and parsing Word (.docx) and PDF (.pdf) files.
Developed by ChimSe (viduvan) - https://github.com/viduvan
Extracted from odin_slides/utils.py for API usage.
"""
import logging
from pathlib import Path
from typing import Callable, Awaitable

from docx import Document

from ..core.config import settings

logger = logging.getLogger("pptx_api.docs")


def read_docx(file_path: str | Path) -> str:
    """
    Read the full text content of a Word document.

    Args:
        file_path: Path to the .docx file.

    Returns:
        Full text content as a single string.
    """
    try:
        doc = Document(str(file_path))
        full_text = ""
        for paragraph in doc.paragraphs:
            full_text += paragraph.text + "\n"
        return full_text
    except Exception as e:
        logger.error(f"Error reading Word file: {e}")
        raise


def read_pdf(file_path: str | Path) -> str:
    """
    Read the full text content of a PDF document.

    Args:
        file_path: Path to the .pdf file.

    Returns:
        Full text content as a single string.
    """
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(str(file_path))
        full_text = ""
        for page in reader.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"
        return full_text
    except Exception as e:
        logger.error(f"Error reading PDF file: {e}")
        raise


def read_big_docx(file_path: str | Path, chunk_size: int) -> list[str]:
    """
    Read a large Word document in chunks.

    Args:
        file_path: Path to the .docx file.
        chunk_size: Maximum number of words per chunk.

    Returns:
        List of text chunks.
    """
    try:
        doc = Document(str(file_path))
        chunks = []
        current_text = ""

        for paragraph in doc.paragraphs:
            paragraph_text = paragraph.text + "\n"
            if len(current_text.split()) + len(paragraph_text.split()) > chunk_size:
                if current_text.strip():
                    chunks.append(current_text)
                current_text = paragraph_text
            else:
                current_text += paragraph_text

        if current_text.strip():
            chunks.append(current_text)

        return chunks
    except Exception as e:
        logger.error(f"Error reading big Word file: {e}")
        raise


def read_big_pdf(file_path: str | Path, chunk_size: int) -> list[str]:
    """
    Read a large PDF document in chunks.

    Args:
        file_path: Path to the .pdf file.
        chunk_size: Maximum number of words per chunk.

    Returns:
        List of text chunks.
    """
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(str(file_path))
        chunks = []
        current_text = ""

        for page in reader.pages:
            page_text = page.extract_text()
            if not page_text:
                continue
            page_text += "\n"
            if len(current_text.split()) + len(page_text.split()) > chunk_size:
                if current_text.strip():
                    chunks.append(current_text)
                current_text = page_text
            else:
                current_text += page_text

        if current_text.strip():
            chunks.append(current_text)

        return chunks
    except Exception as e:
        logger.error(f"Error reading big PDF file: {e}")
        raise


def _split_text_into_chunks(text: str, chunk_size: int) -> list[str]:
    """
    Split already-extracted text into word-count-based chunks.

    Splits on paragraph boundaries (double newlines) to preserve structure.
    """
    paragraphs = text.split("\n")
    chunks = []
    current_text = ""

    for para in paragraphs:
        line = para + "\n"
        if len(current_text.split()) + len(line.split()) > chunk_size:
            if current_text.strip():
                chunks.append(current_text)
            current_text = line
        else:
            current_text += line

    if current_text.strip():
        chunks.append(current_text)

    return chunks


async def process_document(
    file_path: str | Path,
    summarize_fn: Callable[[str], Awaitable[str]],
) -> tuple[str, bool]:
    """
    Process a document: read it, and apply hierarchical summarization if too large.

    For documents exceeding MAX_WORD_COUNT_WITHOUT_SUMMARIZATION:
    1. Split into chunks of SUMMARIZE_CHUNK_SIZE words
    2. Summarize each chunk individually
    3. If combined result still exceeds the limit, re-chunk and re-summarize (recursive)
    4. Repeat up to MAX_SUMMARIZE_ROUNDS times

    Args:
        file_path: Path to the .docx or .pdf file.
        summarize_fn: Async function to summarize text chunks.

    Returns:
        Tuple of (processed_content, was_summarized).
    """
    MAX_SUMMARIZE_ROUNDS = 3

    file_ext = Path(file_path).suffix.lower()
    if file_ext == ".pdf":
        word_content = read_pdf(file_path)
    else:
        word_content = read_docx(file_path)

    word_count = len(word_content.split())
    logger.info(f"Document read: {word_count} words (limit: {settings.MAX_WORD_COUNT_WITHOUT_SUMMARIZATION})")

    if word_count <= settings.MAX_WORD_COUNT_WITHOUT_SUMMARIZATION:
        return word_content, False

    # ── Hierarchical summarization ──────────────────────────
    current_text = word_content
    chunk_size = settings.SUMMARIZE_CHUNK_SIZE

    for round_num in range(1, MAX_SUMMARIZE_ROUNDS + 1):
        current_word_count = len(current_text.split())
        logger.info(
            f"Summarization round {round_num}: {current_word_count} words, "
            f"chunk_size={chunk_size}"
        )

        # Split into chunks (first round uses file-based reader, subsequent use text splitter)
        if round_num == 1:
            if file_ext == ".pdf":
                chunks = read_big_pdf(file_path, chunk_size)
            else:
                chunks = read_big_docx(file_path, chunk_size)
        else:
            chunks = _split_text_into_chunks(current_text, chunk_size)

        logger.info(f"  → Split into {len(chunks)} chunks")

        # Summarize each chunk
        summarized_chunks = []
        for i, chunk in enumerate(chunks, 1):
            logger.debug(f"  → Summarizing chunk {i}/{len(chunks)} ({len(chunk.split())} words)")
            summary = await summarize_fn(chunk)
            summarized_chunks.append(summary)

        current_text = "\n\n".join(summarized_chunks)
        new_word_count = len(current_text.split())
        logger.info(
            f"  → After round {round_num}: {new_word_count} words "
            f"(reduced {current_word_count - new_word_count} words, "
            f"{((current_word_count - new_word_count) / current_word_count * 100):.0f}%)"
        )

        # Check if we're within the limit
        if new_word_count <= settings.MAX_CONTENT_FOR_LLM:
            logger.info(f"Summarization complete after {round_num} round(s): {new_word_count} words")
            return current_text, True

        # If reduction was minimal (<20%), stop to avoid infinite loop
        reduction_ratio = (current_word_count - new_word_count) / current_word_count
        if reduction_ratio < 0.2:
            logger.warning(
                f"Summarization stalled at round {round_num} "
                f"(only {reduction_ratio:.0%} reduction). Stopping."
            )
            break

    # Final safety: truncate if still too large
    final_word_count = len(current_text.split())
    if final_word_count > settings.MAX_CONTENT_FOR_LLM:
        logger.warning(
            f"Content still {final_word_count} words after {MAX_SUMMARIZE_ROUNDS} rounds. "
            f"Truncating to {settings.MAX_CONTENT_FOR_LLM} words."
        )
        words = current_text.split()
        current_text = " ".join(words[:settings.MAX_CONTENT_FOR_LLM])

    return current_text, True
