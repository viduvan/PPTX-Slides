"""
Image Service — Logic for searching and downloading images via Pixabay API.
Developed by ChimSe (viduvan) - https://github.com/viduvan

Falls back gracefully: returns None if no API key or fetch fails.
Enhanced with topic-aware keyword validation and relevance filtering.
"""
import asyncio
import logging
import hashlib
from pathlib import Path

import aiohttp

from ..core.config import settings

logger = logging.getLogger("odin_api.services.image_service")

PIXABAY_API_URL = "https://pixabay.com/api/"

# Generic keywords that produce irrelevant results when used alone
GENERIC_KEYWORDS = {
    "person", "man", "woman", "people", "girl", "boy", "lady", "guy",
    "landscape", "nature", "building", "city", "field", "flag",
    "leader", "soldier", "office", "team", "sky", "road", "mountain",
    "water", "tree", "flower", "animal", "food", "car", "house",
    "background", "abstract", "pattern", "texture", "light",
}


def _detect_vietnam_topic(document_topic: str) -> str:
    """
    Detect which Vietnamese topic the document is about.
    Returns: 'history', 'literature', 'geography', or '' if none detected.
    """
    if not document_topic:
        return ""
    topic_lower = document_topic.lower()

    # Vietnamese history markers
    history_markers = [
        "lịch sử", "history", "hồ chí minh", "ho chi minh", "bác hồ",
        "kháng chiến", "cách mạng", "giải phóng", "chiến dịch", "khởi nghĩa",
        "triều đại", "nhà trần", "nhà lý", "nhà lê", "nhà nguyễn",
        "trần hưng đạo", "lê lợi", "quang trung", "điện biên phủ",
        "hai bà trưng", "võ nguyên giáp", "thực dân", "đế quốc",
        "phong kiến", "bắc thuộc", "đại việt", "văn lang",
    ]
    if any(m in topic_lower for m in history_markers):
        return "history"

    # Vietnamese literature markers
    literature_markers = [
        "văn học", "literature", "thơ", "poetry", "truyện kiều", "kiều",
        "nguyễn du", "nam cao", "chí phèo", "tắt đèn", "lão hạc",
        "xuân diệu", "hàn mặc tử", "tố hữu", "hồ xuân hương",
        "lục vân tiên", "ca dao", "tục ngữ", "truyện ngắn", "tiểu thuyết",
        "tác phẩm", "nhà văn", "nhà thơ", "phân tích", "bình giảng",
        "bình ngô đại cáo", "hịch tướng sĩ", "chinh phụ ngâm",
        "vợ nhặt", "vợ chồng a phủ", "số đỏ", "dế mèn",
        "nguyễn nhật ánh", "mắt biếc", "truyện cổ tích",
        "lục bát", "thơ mới", "tự lực văn đoàn", "văn học dân gian",
    ]
    if any(m in topic_lower for m in literature_markers):
        return "literature"

    # Vietnamese geography markers
    geography_markers = [
        "địa lý", "địa lí", "geography", "địa hình", "khí hậu",
        "sông hồng", "sông mê kông", "mekong", "sông cửu long",
        "đồng bằng sông", "tây nguyên", "trường sơn", "fansipan",
        "vịnh hạ long", "phong nha", "biển đông",
        "bắc bộ", "trung bộ", "nam bộ",
        "miền bắc", "miền trung", "miền nam",
        "đồng bằng", "châu thổ", "phù sa", "lúa nước",
        "bản đồ", "lãnh thổ", "hình chữ s",
        "vùng kinh tế", "dân cư", "dân số",
    ]
    if any(m in topic_lower for m in geography_markers):
        return "geography"

    # General Vietnam reference
    if any(m in topic_lower for m in ["việt nam", "vietnam"]):
        return "vietnam"

    return ""


# Keywords that produce politically sensitive or irrelevant images
_SENSITIVE_KEYWORDS = {
    "soldier", "military", "army", "troops", "war", "battle",
    "flag", "banner", "march", "revolution", "independence",
}


def _enhance_keyword(keyword: str, document_topic: str) -> str:
    """
    Enhance a generic keyword by prefixing with the document topic.
    Special handling for Vietnamese history, literature, and geography content.
    """
    if not keyword:
        return keyword

    words = set(keyword.lower().split())
    vn_topic = _detect_vietnam_topic(document_topic)

    # For Vietnamese topics: force "Vietnam" prefix if not present
    if vn_topic:
        keyword_lower = keyword.lower()
        has_vietnam_ref = any(w in keyword_lower for w in [
            "vietnam", "hanoi", "saigon", "hue", "ho chi minh",
            "dien bien", "ba dinh", "thang long", "vietnamese",
            "mekong", "ha long", "da lat", "sapa", "phong nha",
        ])
        if not has_vietnam_ref:
            # Add "Vietnam" prefix for context
            if words & (_SENSITIVE_KEYWORDS | GENERIC_KEYWORDS):
                enhanced = f"Vietnam {keyword}"
                logger.info(f"Vietnam {vn_topic}: enhanced '{keyword}' → '{enhanced}'")
                return enhanced

    # If ALL words are generic, prefix with topic for specificity
    if words and words.issubset(GENERIC_KEYWORDS) and document_topic:
        enhanced = f"{document_topic} {keyword}"
        logger.info(f"Enhanced generic keyword: '{keyword}' → '{enhanced}'")
        return enhanced

    # If any word is generic but not all, still usable
    return keyword


def _score_image_relevance(hit: dict, keyword: str, document_topic: str) -> int:
    """
    Score a Pixabay result for relevance to the keyword and document topic.
    Higher score = more relevant.
    """
    score = 0
    tags = hit.get("tags", "").lower()
    keyword_lower = keyword.lower()

    # Check if keyword words appear in image tags
    for word in keyword_lower.split():
        if len(word) > 2 and word in tags:
            score += 3

    # Bonus for document topic words appearing in tags
    if document_topic:
        for word in document_topic.lower().split():
            if len(word) > 2 and word in tags:
                score += 2

    # Prefer images with higher resolution (likely more professional)
    if hit.get("imageWidth", 0) >= 1920:
        score += 1

    # Prefer images with more likes (usually better quality)
    if hit.get("likes", 0) > 50:
        score += 1

    return score


async def _search_and_download(session: aiohttp.ClientSession, keyword: str,
                                api_key: str,
                                document_topic: str = "") -> Path | None:
    """Search Pixabay and download the most relevant image for the given keyword."""
    cache_name = hashlib.md5(keyword.encode()).hexdigest() + ".jpg"
    cache_path = settings.IMAGES_DIR / cache_name
    if cache_path.exists():
        logger.debug(f"Cache hit for '{keyword}'")
        return cache_path

    params = {
        "key": api_key,
        "q": keyword,
        "image_type": "photo",
        "orientation": "horizontal",
        "min_width": 640,
        "per_page": 10,  # Fetch more results to pick the best one
        "safesearch": "true",
    }

    try:
        async with session.get(PIXABAY_API_URL, params=params,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                logger.warning(f"Pixabay API {resp.status} for '{keyword}'")
                return None
            data = await resp.json()

        hits = data.get("hits", [])
        if not hits:
            logger.info(f"No images for '{keyword}'")
            return None

        # Score all results and pick the best match
        if document_topic and len(hits) > 1:
            scored = [(hit, _score_image_relevance(hit, keyword, document_topic))
                      for hit in hits]
            scored.sort(key=lambda x: x[1], reverse=True)
            best_hit = scored[0][0]
            best_score = scored[0][1]
            logger.debug(
                f"Best image for '{keyword}': score={best_score}, "
                f"tags='{best_hit.get('tags', '')[:60]}'"
            )
        else:
            best_hit = hits[0]

        image_url = best_hit.get("webformatURL", "")
        if not image_url:
            return None

        async with session.get(image_url,
                               timeout=aiohttp.ClientTimeout(total=15)) as img_resp:
            if img_resp.status != 200:
                logger.warning(f"Image download failed: {img_resp.status}")
                return None

            settings.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
            image_data = await img_resp.read()
            cache_path.write_bytes(image_data)
            logger.info(f"Downloaded '{keyword}' → {cache_path.name} ({len(image_data)} bytes)")
            return cache_path

    except Exception as e:
        logger.warning(f"Error fetching '{keyword}': {e}")
        return None


def _extract_fallback_keyword(slide_data: dict, document_topic: str = "") -> str:
    """Extract a smart fallback keyword from the slide title, using document topic for context."""
    title = slide_data.get("title", "")
    # Take the first 1-2 meaningful words from title
    stop_words = {"the", "a", "an", "of", "and", "in", "for", "to", "on", "is",
                  "are", "was", "with", "by", "at", "from", "as", "các", "và",
                  "cho", "về", "của", "trên", "trong", "để", "là", "có", "được",
                  "một", "những", "này", "với", "không", "theo", "từ", "đến",
                  "bài", "phần", "chương", "mục", "slide"}
    words = [w for w in title.split() if w.lower() not in stop_words and len(w) > 2]
    fallback = " ".join(words[:2]) if words else "presentation"

    # Prefix with document topic if fallback is too generic
    if fallback.lower() in GENERIC_KEYWORDS and document_topic:
        fallback = f"{document_topic} {fallback}"

    return fallback


async def fetch_images_for_slides(slides: list[dict],
                                   document_topic: str = "") -> dict:
    """
    Fetch images for all slides. Uses a single session to avoid connection issues.
    Falls back to title-derived keywords if image_keyword fails.
    Enhanced with topic-aware keyword validation and relevance scoring.

    Args:
        slides: List of slide dicts with image_keyword field.
        document_topic: Overall document topic for keyword context and relevance scoring.
    """
    api_key = settings.PIXABAY_API_KEY
    if not api_key:
        logger.debug("No PIXABAY_API_KEY, skipping all image fetches")
        return {}

    if document_topic:
        logger.info(f"Image search with document topic context: '{document_topic[:60]}'")

    image_paths = {}

    async with aiohttp.ClientSession() as session:
        for slide_data in slides:
            slide_num = slide_data.get("slide_number")
            keyword = slide_data.get("image_keyword", "").strip()

            if keyword:
                # Enhance generic keywords with topic context
                enhanced_keyword = _enhance_keyword(keyword, document_topic)
                logger.info(f"Slide {slide_num}: image_keyword='{keyword}'"
                           + (f" → enhanced='{enhanced_keyword}'" if enhanced_keyword != keyword else ""))

                img_path = await _search_and_download(
                    session, enhanced_keyword, api_key, document_topic
                )
                if img_path:
                    image_paths[slide_num] = str(img_path)
                    await asyncio.sleep(0.3)  # Rate limit buffer
                    continue

                # If enhanced keyword failed and was different, try original
                if enhanced_keyword != keyword:
                    img_path = await _search_and_download(
                        session, keyword, api_key, document_topic
                    )
                    if img_path:
                        image_paths[slide_num] = str(img_path)
                        await asyncio.sleep(0.3)
                        continue

            # Fallback: use words from title + document topic
            fallback = _extract_fallback_keyword(slide_data, document_topic)
            if fallback and fallback != keyword:
                logger.info(f"Trying fallback keyword '{fallback}' for slide {slide_num}")
                img_path = await _search_and_download(
                    session, fallback, api_key, document_topic
                )
                if img_path:
                    image_paths[slide_num] = str(img_path)

            await asyncio.sleep(0.3)  # Rate limit buffer

    logger.info(f"Fetched {len(image_paths)} images for {len(slides)} slides")
    return image_paths
