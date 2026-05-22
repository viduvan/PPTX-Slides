"""
Writer Agent Service — Data processor for slide content.
NO LLM calls — only section preparation, JSON parsing, and slide merging.
All LLM calls happen in n8n workflow nodes.

Developed by ChimSe (viduvan) - https://github.com/viduvan
"""
import json
import logging
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("writer")

app = FastAPI(title="PPTX-Slides Writer Agent", version="1.0.0")

SHARED_DATA_DIR = Path(os.getenv("SHARED_DATA_DIR", "/data"))


# ── Request/Response Models ──────────────────────────────────

class PrepareSectionsRequest(BaseModel):
    job_id: str
    outline: list[dict]
    chunk_mapping: dict
    chunks: list[str] = []
    volume_path: str = ""


class PrepareSectionsResponse(BaseModel):
    job_id: str
    sections: list[dict]


class MergeSlidesRequest(BaseModel):
    job_id: str
    all_section_slides: list[dict]


class MergeSlidesResponse(BaseModel):
    job_id: str
    slides: list[dict]
    total_slides: int


class ParseResponseRequest(BaseModel):
    text: str


# ── Endpoints ────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "agent": "writer"}


@app.post("/prepare-sections", response_model=PrepareSectionsResponse)
async def prepare_sections(req: PrepareSectionsRequest):
    """
    Map outline sections to their original text chunks.
    Each section gets the ORIGINAL chunk text for writing.
    """
    outline = req.outline
    chunk_mapping = req.chunk_mapping
    chunks = req.chunks

    # Load chunks from volume if not in body
    if not chunks and req.volume_path:
        chunks = _load_chunks_from_volume(req.volume_path)

    if not chunks:
        raise HTTPException(status_code=400, detail="No chunks provided")

    sections = []
    for section in outline:
        section_id = str(section.get("section_id", 0))
        mapped_chunk_ids = chunk_mapping.get(section_id, [])

        # Collect original text from mapped chunks
        section_text_parts = []
        for cid in mapped_chunk_ids:
            idx = int(cid)
            if 0 <= idx < len(chunks):
                section_text_parts.append(chunks[idx])

        # If no mapping, try to assign based on position
        if not section_text_parts:
            total_sections = len(outline)
            total_chunks = len(chunks)
            if total_chunks > 0 and total_sections > 0:
                start = int(section_id) * total_chunks // total_sections
                end = (int(section_id) + 1) * total_chunks // total_sections
                section_text_parts = chunks[start:max(start + 1, end)]

        section_text = "\n\n".join(section_text_parts)

        sections.append({
            "section_id": section.get("section_id", 0),
            "section_title": section.get("title", ""),
            "num_slides": section.get("slides", 2),
            "key_points": section.get("key_points", []),
            "section_text": section_text,
            "chunk_ids": mapped_chunk_ids,
            "text_word_count": len(section_text.split()),
        })

    logger.info(
        f"[{req.job_id}] Prepared {len(sections)} sections, "
        f"total text: {sum(s['text_word_count'] for s in sections)} words"
    )
    return PrepareSectionsResponse(job_id=req.job_id, sections=sections)


@app.post("/parse-response")
async def parse_response(req: ParseResponseRequest):
    """
    Parse LLM JSON response into validated slides array.
    Handles common JSON issues from LLM output.
    """
    text = req.text
    if not text:
        return {"slides": [], "error": "Empty response"}

    # Try direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            parsed = [parsed]
        slides = _validate_slides(parsed)
        return {"slides": slides}
    except json.JSONDecodeError:
        pass

    # Try extracting JSON array from text
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            slides = _validate_slides(parsed)
            return {"slides": slides}
        except json.JSONDecodeError:
            pass

    # Try fixing trailing commas
    fixed = re.sub(r',\s*([}\]])', r'\1', text)
    try:
        parsed = json.loads(fixed)
        if isinstance(parsed, dict):
            parsed = [parsed]
        slides = _validate_slides(parsed)
        return {"slides": slides, "repaired": True}
    except json.JSONDecodeError:
        pass

    return {"slides": [], "error": "Failed to parse LLM response"}


@app.post("/merge-slides", response_model=MergeSlidesResponse)
async def merge_slides(req: MergeSlidesRequest):
    """
    Merge slides from all sections, deduplicate, and renumber.
    """
    all_slides = []
    for section_data in req.all_section_slides:
        section_slides = section_data.get("section_slides", [])
        if isinstance(section_slides, list):
            all_slides.extend(section_slides)

    # Deduplicate by title similarity
    seen_titles = set()
    unique_slides = []
    for slide in all_slides:
        title = slide.get("title", "").strip().lower()
        if title and title in seen_titles:
            logger.warning(f"[{req.job_id}] Removing duplicate slide: '{title[:50]}'")
            continue
        if title:
            seen_titles.add(title)
        unique_slides.append(slide)

    # Renumber sequentially
    for i, slide in enumerate(unique_slides, 1):
        slide["slide_number"] = i

    # Validate content density
    for slide in unique_slides:
        content = slide.get("content", "")
        bullets = [b for b in content.split("\n") if b.strip().startswith("-")]
        if len(bullets) < 3:
            logger.warning(
                f"[{req.job_id}] Slide {slide['slide_number']} '{slide.get('title', '')[:30]}' "
                f"has only {len(bullets)} bullets (min 5 recommended)"
            )

    logger.info(
        f"[{req.job_id}] Merged {len(all_slides)} → {len(unique_slides)} slides "
        f"({len(all_slides) - len(unique_slides)} duplicates removed)"
    )
    return MergeSlidesResponse(
        job_id=req.job_id,
        slides=unique_slides,
        total_slides=len(unique_slides),
    )


# ── Helpers ──────────────────────────────────────────────────

def _load_chunks_from_volume(volume_path: str) -> list[str]:
    """Load chunks from shared volume."""
    path = Path(volume_path)
    if not path.exists():
        return []

    meta_file = path / "meta.json"
    if meta_file.exists():
        meta = json.loads(meta_file.read_text())
        chunk_count = meta.get("chunk_count", 0)
    else:
        chunk_count = len(list(path.glob("chunk_*.txt")))

    chunks = []
    for i in range(chunk_count):
        chunk_file = path / f"chunk_{i}.txt"
        if chunk_file.exists():
            chunks.append(chunk_file.read_text(encoding="utf-8"))
    return chunks


def _validate_slides(slides: list) -> list[dict]:
    """Validate and normalize slide data."""
    validated = []
    for slide in slides:
        if not isinstance(slide, dict):
            continue
        # Ensure required fields
        slide.setdefault("title", "")
        slide.setdefault("content", "")
        slide.setdefault("narration", "")
        slide.setdefault("image_keyword", "")
        slide.setdefault("slide_number", 0)
        # Clean content - convert list to bullet string
        if isinstance(slide["content"], list):
            slide["content"] = "\n".join(f"- {item}" for item in slide["content"])
        validated.append(slide)
    return validated
