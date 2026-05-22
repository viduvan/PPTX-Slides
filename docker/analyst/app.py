"""
Analyst Agent Service — Data processor for document analysis.
NO LLM calls — only text splitting, metadata merging, and file I/O.
All LLM calls happen in n8n workflow nodes.

Developed by ChimSe (viduvan) - https://github.com/viduvan
"""
import json
import logging
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("analyst")

app = FastAPI(title="PPTX-Slides Analyst Agent", version="1.0.0")

SHARED_DATA_DIR = Path(os.getenv("SHARED_DATA_DIR", "/data"))


# ── Request/Response Models ──────────────────────────────────

class SplitChunksRequest(BaseModel):
    text: str
    chunk_size: int = 10000
    job_id: str = ""


class SplitChunksResponse(BaseModel):
    job_id: str
    chunks: list[dict]
    total_chunks: int
    total_words: int


class MergeMetadataRequest(BaseModel):
    job_id: str
    all_metadata: list[dict]


class MergeMetadataResponse(BaseModel):
    job_id: str
    merged_metadata: dict


class SaveChunksRequest(BaseModel):
    job_id: str
    chunks: list[str]


class SaveChunksResponse(BaseModel):
    job_id: str
    volume_path: str
    chunk_count: int


# ── Endpoints ────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "agent": "analyst"}


@app.post("/split-chunks", response_model=SplitChunksResponse)
async def split_chunks(req: SplitChunksRequest):
    """
    Split text into chunks of approximately chunk_size words.
    Splits at paragraph boundaries to preserve document structure.
    """
    text = req.text
    chunk_size = req.chunk_size
    job_id = req.job_id or str(uuid.uuid4())

    if not text.strip():
        raise HTTPException(status_code=400, detail="Text is empty")

    words = text.split()
    total_words = len(words)

    if total_words <= chunk_size:
        # No splitting needed
        return SplitChunksResponse(
            job_id=job_id,
            chunks=[{
                "chunk_index": 0,
                "chunk_text": text,
                "word_count": total_words,
                "total_chunks": 1,
            }],
            total_chunks=1,
            total_words=total_words,
        )

    # Split at paragraph boundaries
    paragraphs = text.split("\n")
    chunks = []
    current_text = ""
    current_words = 0

    for para in paragraphs:
        para_text = para + "\n"
        para_words = len(para_text.split())

        if current_words + para_words > chunk_size and current_text.strip():
            chunks.append(current_text)
            current_text = para_text
            current_words = para_words
        else:
            current_text += para_text
            current_words += para_words

    if current_text.strip():
        chunks.append(current_text)

    total_chunks = len(chunks)
    chunk_dicts = []
    for i, chunk_text in enumerate(chunks):
        chunk_dicts.append({
            "chunk_index": i,
            "chunk_text": chunk_text,
            "word_count": len(chunk_text.split()),
            "total_chunks": total_chunks,
        })

    logger.info(f"[{job_id}] Split {total_words} words → {total_chunks} chunks × ~{chunk_size}")
    return SplitChunksResponse(
        job_id=job_id,
        chunks=chunk_dicts,
        total_chunks=total_chunks,
        total_words=total_words,
    )


@app.post("/merge-metadata", response_model=MergeMetadataResponse)
async def merge_metadata(req: MergeMetadataRequest):
    """
    Merge extracted metadata from multiple chunks into a unified structure.
    Input: array of per-chunk metadata (headings, topics, entities).
    Output: deduplicated, ordered merged metadata.
    """
    all_metadata = req.all_metadata

    if not all_metadata:
        raise HTTPException(status_code=400, detail="No metadata to merge")

    # Collect and deduplicate
    all_headings = []
    all_topics = []
    all_entities = []
    content_types = []
    seen_headings = set()
    seen_topics = set()
    seen_entities = set()

    for item in all_metadata:
        meta = item.get("metadata", item)
        chunk_idx = item.get("chunk_index", 0)

        for h in meta.get("headings", []):
            h_lower = h.strip().lower()
            if h_lower and h_lower not in seen_headings:
                seen_headings.add(h_lower)
                all_headings.append({"text": h.strip(), "chunk": chunk_idx})

        for t in meta.get("topics", []):
            t_lower = t.strip().lower()
            if t_lower and t_lower not in seen_topics:
                seen_topics.add(t_lower)
                all_topics.append({"text": t.strip(), "chunk": chunk_idx})

        for e in meta.get("key_entities", []):
            e_lower = e.strip().lower()
            if e_lower and e_lower not in seen_entities:
                seen_entities.add(e_lower)
                all_entities.append({"text": e.strip(), "chunk": chunk_idx})

        ct = meta.get("content_type", "mixed")
        if ct:
            content_types.append(ct)

    # Detect overall language from headings/topics
    vi_count = sum(1 for h in all_headings if _has_vietnamese(h["text"]))
    language = "vi" if vi_count > len(all_headings) * 0.3 else "en"

    merged = {
        "headings": all_headings,
        "topics": all_topics,
        "key_entities": all_entities,
        "total_chunks": len(all_metadata),
        "content_type": max(set(content_types), key=content_types.count) if content_types else "mixed",
        "language": language,
        "stats": {
            "unique_headings": len(all_headings),
            "unique_topics": len(all_topics),
            "unique_entities": len(all_entities),
        }
    }

    logger.info(
        f"[{req.job_id}] Merged metadata from {len(all_metadata)} chunks: "
        f"{len(all_headings)} headings, {len(all_topics)} topics, {len(all_entities)} entities"
    )
    return MergeMetadataResponse(job_id=req.job_id, merged_metadata=merged)


@app.post("/save-chunks", response_model=SaveChunksResponse)
async def save_chunks(req: SaveChunksRequest):
    """
    Save chunks to shared volume for large documents (>50K words).
    Used by n8n to avoid passing large payloads through HTTP body.
    """
    job_dir = SHARED_DATA_DIR / "jobs" / req.job_id / "chunks"
    job_dir.mkdir(parents=True, exist_ok=True)

    for i, chunk_text in enumerate(req.chunks):
        chunk_file = job_dir / f"chunk_{i}.txt"
        chunk_file.write_text(chunk_text, encoding="utf-8")

    # Save metadata
    meta = {
        "job_id": req.job_id,
        "chunk_count": len(req.chunks),
        "chunk_words": [len(c.split()) for c in req.chunks],
    }
    (job_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    volume_path = str(job_dir)
    logger.info(f"[{req.job_id}] Saved {len(req.chunks)} chunks to {volume_path}")
    return SaveChunksResponse(
        job_id=req.job_id,
        volume_path=volume_path,
        chunk_count=len(req.chunks),
    )


@app.post("/load-chunks")
async def load_chunks(job_id: str):
    """Load chunks from shared volume."""
    job_dir = SHARED_DATA_DIR / "jobs" / job_id / "chunks"
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail=f"Chunks not found for job {job_id}")

    meta_file = job_dir / "meta.json"
    meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
    chunk_count = meta.get("chunk_count", 0)

    chunks = []
    for i in range(chunk_count):
        chunk_file = job_dir / f"chunk_{i}.txt"
        if chunk_file.exists():
            chunks.append(chunk_file.read_text(encoding="utf-8"))

    return {"job_id": job_id, "chunks": chunks, "chunk_count": len(chunks)}


# ── Helpers ──────────────────────────────────────────────────

def _has_vietnamese(text: str) -> bool:
    """Quick check if text contains Vietnamese diacritics."""
    vn_chars = set("àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ")
    return any(c in vn_chars for c in text.lower())
