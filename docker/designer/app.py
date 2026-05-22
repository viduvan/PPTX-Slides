"""
Designer Agent Service — Image fetching and visual asset management.
NO LLM calls — only image search (Pixabay/Wikimedia), download, and caching.
Layout and theme selection happen in n8n AI nodes.

Developed by ChimSe (viduvan) - https://github.com/viduvan
"""
import asyncio
import hashlib
import logging
import os
import random
import re
from pathlib import Path

import aiohttp
from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("designer")

app = FastAPI(title="PPTX-Slides Designer Agent", version="1.0.0")

SHARED_DATA_DIR = Path(os.getenv("SHARED_DATA_DIR", "/data"))
IMAGES_DIR = SHARED_DATA_DIR / "images"
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
PIXABAY_API_URL = "https://pixabay.com/api/"
WIKIMEDIA_API_URL = "https://commons.wikimedia.org/w/api.php"
_USER_AGENT = "PPTX-Slides/1.0 (https://pptx.click)"
_wikimedia_semaphore = asyncio.Semaphore(2)

# ── Blacklist/Whitelist (extracted from image_service.py) ────
GENERIC_KEYWORDS = {
    "person", "man", "woman", "people", "girl", "boy",
    "landscape", "nature", "building", "city", "field", "flag",
    "leader", "soldier", "office", "team", "sky", "road", "mountain",
}

_HISTORY_BLACKLIST_TAGS = {
    "fashion", "model", "sexy", "beauty", "makeup", "glamour",
    "portrait", "selfie", "couple", "wedding", "bikini",
    "motorcycle", "motorbike", "scooter", "car", "traffic",
    "american", "america", "usa", "us flag", "veteran",
    "skyscraper", "corporate", "nightlife", "neon",
    "phone", "laptop", "computer", "technology",
}

_VN_HISTORY_FALLBACK_KEYWORDS = [
    "Vietnam pagoda temple", "Vietnam ancient temple", "Vietnam heritage site",
    "Hanoi old quarter", "Hue imperial citadel", "Vietnam traditional village",
    "Vietnam rice field countryside", "Vietnam Ho Chi Minh mausoleum",
    "Vietnam monument statue", "Vietnam dynasty architecture",
]


# ── Models ───────────────────────────────────────────────────

class FetchImagesRequest(BaseModel):
    job_id: str
    slides: list[dict]
    document_topic: str = ""


class FetchImagesResponse(BaseModel):
    job_id: str
    image_paths: dict[str, str]
    theme: str
    failed_slides: list[int]


# ── Endpoints ────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "agent": "designer", "pixabay": bool(PIXABAY_API_KEY)}


@app.post("/fetch-images", response_model=FetchImagesResponse)
async def fetch_images(req: FetchImagesRequest):
    """
    Fetch images for all slides. Uses Pixabay + Wikimedia Commons.
    Cascading fallback: keyword → enhanced keyword → title fallback → VN history pool.
    """
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    is_vn_history = _is_vietnam_history_topic(req.document_topic)
    image_paths = {}
    failed_slides = []
    used_ids: set[str] = set()
    fallback_idx = 0

    async with aiohttp.ClientSession() as session:
        for slide in req.slides:
            slide_num = slide.get("slide_number", 0)
            keyword = slide.get("image_keyword", "").strip()
            found = False

            if not keyword:
                failed_slides.append(slide_num)
                continue

            enhanced = _enhance_keyword(keyword, req.document_topic, is_vn_history)

            # Attempt 1: Wikimedia (VN history priority)
            if is_vn_history:
                path, img_id = await _search_wikimedia(session, enhanced, used_ids)
                if path and img_id:
                    image_paths[str(slide_num)] = str(path)
                    used_ids.add(img_id)
                    found = True

            # Attempt 2: Pixabay
            if not found and PIXABAY_API_KEY:
                path, img_id = await _search_pixabay(
                    session, enhanced, req.document_topic, is_vn_history, used_ids
                )
                if path and img_id:
                    image_paths[str(slide_num)] = str(path)
                    used_ids.add(str(img_id))
                    found = True

            # Attempt 3: VN history fallback pool
            if not found and is_vn_history:
                fb_keyword = _VN_HISTORY_FALLBACK_KEYWORDS[fallback_idx % len(_VN_HISTORY_FALLBACK_KEYWORDS)]
                fallback_idx += 1
                path, img_id = await _search_pixabay(
                    session, fb_keyword, req.document_topic, True, used_ids
                ) if PIXABAY_API_KEY else (None, None)
                if path and img_id:
                    image_paths[str(slide_num)] = str(path)
                    used_ids.add(str(img_id))
                    found = True

            if not found:
                failed_slides.append(slide_num)

    logger.info(
        f"[{req.job_id}] Images: {len(image_paths)} fetched, "
        f"{len(failed_slides)} failed out of {len(req.slides)} slides"
    )
    return FetchImagesResponse(
        job_id=req.job_id,
        image_paths=image_paths,
        theme="",
        failed_slides=failed_slides,
    )


# ── Image Search Functions (extracted from image_service.py) ─

async def _search_pixabay(
    session: aiohttp.ClientSession, keyword: str,
    document_topic: str, is_history: bool, used_ids: set
) -> tuple[Path | None, int | None]:
    params = {
        "key": PIXABAY_API_KEY, "q": keyword, "image_type": "photo",
        "orientation": "horizontal", "min_width": 640,
        "per_page": 30, "safesearch": "true",
    }
    try:
        async with session.get(PIXABAY_API_URL, params=params,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None, None
            data = await resp.json()

        for hit in data.get("hits", []):
            img_id = hit.get("id")
            if str(img_id) in used_ids:
                continue
            if is_history and _is_blacklisted(hit):
                continue

            image_url = hit.get("webformatURL", "")
            if not image_url:
                continue

            cache_name = f"{hashlib.md5(keyword.encode()).hexdigest()}_{img_id}.jpg"
            cache_path = IMAGES_DIR / cache_name
            if cache_path.exists():
                return cache_path, img_id

            async with session.get(image_url, timeout=aiohttp.ClientTimeout(total=15)) as img_resp:
                if img_resp.status == 200:
                    cache_path.write_bytes(await img_resp.read())
                    return cache_path, img_id
    except Exception as e:
        logger.warning(f"Pixabay error '{keyword}': {e}")
    return None, None


async def _search_wikimedia(
    session: aiohttp.ClientSession, keyword: str, used_ids: set
) -> tuple[Path | None, str | None]:
    cleaned = re.sub(r'[^\w\s]', ' ', keyword, flags=re.UNICODE).strip()[:100]
    if not cleaned:
        return None, None

    params = {
        "action": "query", "generator": "search", "gsrsearch": cleaned,
        "gsrnamespace": 6, "gsrlimit": 10, "prop": "imageinfo",
        "iiprop": "url|size|mime", "iiurlwidth": 800, "format": "json",
    }
    async with _wikimedia_semaphore:
        await asyncio.sleep(random.uniform(1.0, 2.0))
        try:
            async with session.get(WIKIMEDIA_API_URL, params=params,
                                   headers={"User-Agent": _USER_AGENT},
                                   timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return None, None
                data = await resp.json()

            pages = data.get("query", {}).get("pages", {})
            for pid, page in pages.items():
                wm_id = f"wm_{pid}"
                if wm_id in used_ids:
                    continue
                info = (page.get("imageinfo") or [{}])[0]
                if info.get("mime") not in ("image/jpeg", "image/png"):
                    continue
                w, h = info.get("width", 0), info.get("height", 0)
                if w < 500 and h < 500:
                    continue
                thumb_url = info.get("thumburl") or info.get("url")
                if not thumb_url:
                    continue

                cache_name = f"wm_{hashlib.md5(cleaned.encode()).hexdigest()}_{pid}.jpg"
                cache_path = IMAGES_DIR / cache_name
                if cache_path.exists():
                    return cache_path, wm_id

                await asyncio.sleep(random.uniform(0.5, 1.0))
                async with session.get(thumb_url, headers={"User-Agent": _USER_AGENT},
                                       timeout=aiohttp.ClientTimeout(total=15)) as img_resp:
                    if img_resp.status == 200:
                        cache_path.write_bytes(await img_resp.read())
                        return cache_path, wm_id
        except Exception as e:
            logger.warning(f"Wikimedia error '{cleaned}': {e}")
    return None, None


def _is_blacklisted(hit: dict) -> bool:
    tags = {t.strip().lower() for t in hit.get("tags", "").split(",")}
    return bool(tags & _HISTORY_BLACKLIST_TAGS)


def _enhance_keyword(keyword: str, topic: str, is_vn: bool) -> str:
    kw_lower = keyword.lower()
    if is_vn and not any(w in kw_lower for w in ["vietnam", "hanoi", "saigon", "hue"]):
        words = set(kw_lower.split())
        if words & (GENERIC_KEYWORDS | {"soldier", "war", "revolution", "flag"}):
            return f"Vietnam {keyword}"
    if set(kw_lower.split()).issubset(GENERIC_KEYWORDS) and topic:
        return f"{topic} {keyword}"
    return keyword


def _is_vietnam_history_topic(topic: str) -> bool:
    if not topic:
        return False
    t = topic.lower()
    markers = ["việt nam", "vietnam", "lịch sử", "hồ chí minh", "điện biên phủ",
               "kháng chiến", "triều đại", "nhà trần", "nhà lý", "tây sơn"]
    return any(m in t for m in markers)
