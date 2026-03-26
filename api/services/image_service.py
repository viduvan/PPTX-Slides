"""
Image Service — Logic for searching and downloading images.
Developed by ChimSe (viduvan) - https://github.com/viduvan

Supports multiple image providers:
  - Wikimedia Commons (primary for Vietnamese history — no API key needed)
  - Pixabay (general content — requires PIXABAY_API_KEY)

Anti-ban protections for Wikimedia:
  - Semaphore to limit concurrent requests
  - Random delay between requests
  - Retry with exponential backoff on failures
  - Handle 429 (Too Many Requests) with Retry-After
  - In-memory search result cache (avoid duplicate API calls)
  - Keyword sanitization
  - Consistent User-Agent header
"""
import asyncio
import logging
import hashlib
import random
import re
import time
from pathlib import Path

import aiohttp

from ..core.config import settings

logger = logging.getLogger("odin_api.services.image_service")

PIXABAY_API_URL = "https://pixabay.com/api/"
WIKIMEDIA_API_URL = "https://commons.wikimedia.org/w/api.php"

# ── Anti-ban: Consistent User-Agent for ALL requests ────────
_USER_AGENT = "PPTX-Slides/1.0 (https://pptx.click; contact: phamvanviet1104@gmail.com)"

# ── Anti-ban: Semaphore to limit concurrent Wikimedia requests ──
_wikimedia_semaphore = asyncio.Semaphore(2)  # max 2 concurrent requests

# ── Anti-ban: In-memory search result cache ─────────────────
_search_cache: dict[str, tuple[float, dict]] = {}  # key → (timestamp, data)
_CACHE_TTL_SECONDS = 300  # 5 minutes

# Generic keywords that produce irrelevant results when used alone
GENERIC_KEYWORDS = {
    "person", "man", "woman", "people", "girl", "boy", "lady", "guy",
    "landscape", "nature", "building", "city", "field", "flag",
    "leader", "soldier", "office", "team", "sky", "road", "mountain",
    "water", "tree", "flower", "animal", "food", "car", "house",
    "background", "abstract", "pattern", "texture", "light",
}

# Tags that indicate IRRELEVANT images for Vietnamese history content
_HISTORY_BLACKLIST_TAGS = {
    # Fashion & Models & Body
    "fashion", "model", "sexy", "beauty", "makeup", "glamour",
    "portrait", "selfie", "couple", "wedding", "love", "romantic",
    "posing", "attractive", "pretty", "handsome",
    "bikini", "lingerie", "dress", "outfit", "hairstyle",
    "woman", "girl", "young woman", "young girl",
    # Vehicles (expanded)
    "motorcycle", "motorbike", "scooter", "bike", "moped",
    "moto", "rider", "helmet", "biker", "cycling",
    "car", "automobile", "vehicle", "traffic", "driving",
    "commuter", "transport", "taxi", "bus",
    # American/Foreign imagery (critical - Pixabay returns US Vietnam Memorial!)
    "american", "america", "usa", "united states", "us flag",
    "washington", "dc", "stars and stripes", "us memorial",
    "veteran", "veterans", "veterans day", "memorial day",
    # Modern city/business/tourism
    "skyscraper", "modern", "business", "corporate", "office",
    "shopping", "mall", "restaurant", "cafe", "bar",
    "nightlife", "neon", "skyline", "downtown",
    "urban", "street", "street life", "street food",
    "tourism", "tourist", "traveler", "backpacker",
    "hostel", "hotel", "resort", "beach party", "nightclub",
    # Technology
    "phone", "laptop", "computer", "technology", "digital",
    "smartphone", "tablet", "internet", "social media",
    # Other irrelevant
    "food", "cooking", "recipe", "fitness", "gym", "sport",
    "pet", "dog", "cat", "christmas", "halloween",
    "sunset", "sunrise", "aerial", "drone",
}

# Whitelist: images MUST have at least one of these tags for VN history content
_HISTORY_WHITELIST_TAGS = {
    "vietnam", "vietnamese", "hanoi", "saigon", "ho chi minh",
    "temple", "pagoda", "shrine", "monument", "statue",
    "memorial", "ancient", "heritage", "historical", "history",
    "traditional", "culture", "architecture", "museum",
    "flag", "red", "star", "asia", "asian", "southeast asia",
    "rice", "paddy", "countryside", "rural", "village",
    "colonial", "french", "indochina", "war", "military",
    "palace", "citadel", "gate", "bridge", "river",
    "bamboo", "conical hat", "ao dai", "lantern",
    "hue", "da nang", "nha trang", "ha long",
}

# Curated safe fallback keywords for Vietnamese history — guaranteed to return relevant images
_VN_HISTORY_FALLBACK_KEYWORDS = [
    "Vietnam pagoda temple",
    "Vietnam ancient temple",
    "Vietnam heritage site",
    "Hanoi old quarter",
    "Hue imperial citadel",
    "Vietnam traditional village",
    "Vietnam rice field countryside",
    "Vietnam Ho Chi Minh mausoleum",
    "Vietnam flag red star",
    "Vietnam monument statue",
    "Vietnam dynasty architecture",
    "Vietnam museum history",
    "Vietnam lantern traditional",
    "Vietnam conical hat culture",
    "Vietnam river landscape",
    "Thang Long Hanoi ancient",
    "Vietnam bamboo village",
    "Vietnam ao dai tradition",
    "Vietnam war memorial monument",
    "Dien Bien Phu valley",
]


def _parse_tags(hit: dict) -> set[str]:
    """Parse Pixabay tags into a set of individual lowercased tags."""
    raw = hit.get("tags", "")
    return {t.strip().lower() for t in raw.split(",") if t.strip()}


def _is_blacklisted_image(hit: dict, is_history: bool) -> bool:
    """
    Check if an image should be rejected based on blacklist.
    Uses comma-split individual tag matching for accuracy.
    Whitelist is now handled via scoring bonus, not hard rejection.
    """
    if not is_history:
        return False

    individual_tags = _parse_tags(hit)
    raw_tags = hit.get("tags", "").lower()

    # BLACKLIST: reject if ANY blacklisted word is an individual tag
    # OR appears as substring in the raw tags string (catch compound tags)
    for bad_tag in _HISTORY_BLACKLIST_TAGS:
        # Check individual tags first (exact match per tag)
        if bad_tag in individual_tags:
            logger.debug(f"Blacklisted (exact): '{bad_tag}' in tags '{raw_tags[:60]}'")
            return True
        # Also check substring for multi-word bad tags
        if " " in bad_tag and bad_tag in raw_tags:
            logger.debug(f"Blacklisted (substr): '{bad_tag}' in tags '{raw_tags[:60]}'")
            return True

    # Check if ANY individual tag contains a blacklisted word as a component
    for tag in individual_tags:
        tag_words = set(tag.split())
        for bad_tag in _HISTORY_BLACKLIST_TAGS:
            if bad_tag in tag_words:
                logger.debug(f"Blacklisted (word): '{bad_tag}' in tag '{tag}'")
                return True

    return False


def _is_vietnam_history_topic(document_topic: str) -> bool:
    """Check if the document topic is related to Vietnamese history."""
    if not document_topic:
        return False
    topic_lower = document_topic.lower()
    vn_markers = [
        "việt nam", "vietnam", "lịch sử", "history",
        "hồ chí minh", "ho chi minh", "bác hồ", "nguyễn ái quốc",
        "võ nguyên giáp", "trần hưng đạo", "lê lợi", "quang trung", "nguyễn huệ",
        "hai bà trưng", "bà triệu", "lý thường kiệt", "nguyễn trãi",
        "phan bội châu", "phan châu trinh", "ngô quyền", "đinh bộ lĩnh",
        "lý công uẩn", "trần nhân tông", "lê thánh tông",
        "điện biên phủ", "cách mạng", "kháng chiến", "giải phóng",
        "bạch đằng", "chi lăng", "đống đa", "rạch gầm",
        "triều đại", "nhà trần", "nhà lý", "nhà lê", "nhà nguyễn",
        "tây sơn", "đại việt", "văn lang", "phong kiến", "bắc thuộc",
        "thực dân pháp", "đế quốc mỹ", "chống pháp", "chống mỹ",
        "chiến dịch", "khởi nghĩa", "anh hùng", "liệt sĩ",
    ]
    return any(marker in topic_lower for marker in vn_markers)


# Keywords that produce politically sensitive or irrelevant images for Vietnamese history
_HISTORY_SENSITIVE_KEYWORDS = {
    "soldier", "military", "army", "troops", "war", "battle",
    "flag", "banner", "march", "revolution", "independence",
}


def _enhance_keyword(keyword: str, document_topic: str) -> str:
    """
    Enhance a generic keyword by prefixing with the document topic.
    Special handling for Vietnamese history content.
    """
    if not keyword:
        return keyword

    words = set(keyword.lower().split())
    is_vn_history = _is_vietnam_history_topic(document_topic)

    # For Vietnamese history: force "Vietnam" prefix if not present
    if is_vn_history:
        keyword_lower = keyword.lower()
        has_vietnam_ref = any(w in keyword_lower for w in [
            "vietnam", "hanoi", "saigon", "hue", "ho chi minh",
            "dien bien", "ba dinh", "thang long", "vietnamese",
        ])
        if not has_vietnam_ref:
            if words & (_HISTORY_SENSITIVE_KEYWORDS | GENERIC_KEYWORDS):
                enhanced = f"Vietnam {keyword}"
                logger.info(f"Vietnam history: enhanced '{keyword}' → '{enhanced}'")
                return enhanced

    # If ALL words are generic, prefix with topic for specificity
    if words and words.issubset(GENERIC_KEYWORDS) and document_topic:
        enhanced = f"{document_topic} {keyword}"
        logger.info(f"Enhanced generic keyword: '{keyword}' → '{enhanced}'")
        return enhanced

    return keyword


def _score_image_relevance(hit: dict, keyword: str, document_topic: str,
                           is_history: bool = False) -> int:
    """
    Score a Pixabay result for relevance to the keyword and document topic.
    Higher score = more relevant.
    Whitelist matching is now integrated here as a bonus instead of hard reject.
    """
    score = 0
    individual_tags = _parse_tags(hit)
    raw_tags = hit.get("tags", "").lower()
    keyword_lower = keyword.lower()

    # Check if keyword words appear in image tags
    for word in keyword_lower.split():
        if len(word) > 2 and word in raw_tags:
            score += 3

    # Bonus for document topic words appearing in tags
    if document_topic:
        for word in document_topic.lower().split():
            if len(word) > 2 and word in raw_tags:
                score += 2

    # WHITELIST BONUS: reward images with history-relevant tags
    # (replaces old hard-reject whitelist)
    if is_history:
        whitelist_matches = 0
        for good_tag in _HISTORY_WHITELIST_TAGS:
            if good_tag in raw_tags or good_tag in individual_tags:
                whitelist_matches += 1
        score += whitelist_matches * 3  # Strong bonus per whitelist match

        # Extra bonus for images explicitly tagged with Vietnam-related terms
        vietnam_bonus_tags = {"vietnam", "vietnamese", "hanoi", "saigon",
                              "ho chi minh", "hue", "da nang"}
        for vt in vietnam_bonus_tags:
            if vt in raw_tags:
                score += 5
                break

    # Bonus for history-related tags (general)
    history_tags = {"temple", "monument", "statue", "memorial", "ancient",
                    "heritage", "traditional", "historical", "pagoda",
                    "architecture", "old", "culture", "museum"}
    for htag in history_tags:
        if htag in individual_tags:
            score += 2

    # Prefer images with higher resolution
    if hit.get("imageWidth", 0) >= 1920:
        score += 1

    # Prefer images with more likes
    if hit.get("likes", 0) > 50:
        score += 1

    return score


async def _search_and_download(session: aiohttp.ClientSession, keyword: str,
                                api_key: str,
                                document_topic: str = "",
                                used_image_ids: set = None) -> tuple[Path | None, int | None]:
    """
    Search Pixabay and download the most relevant UNUSED image.

    Returns:
        Tuple of (image_path, pixabay_image_id) or (None, None) if no suitable image found.
    """
    if used_image_ids is None:
        used_image_ids = set()

    is_history = _is_vietnam_history_topic(document_topic)

    # Check cache first — but only if keyword hasn't been used before
    cache_name = hashlib.md5(keyword.encode()).hexdigest() + ".jpg"
    cache_path = settings.IMAGES_DIR / cache_name

    params = {
        "key": api_key,
        "q": keyword,
        "image_type": "photo",
        "orientation": "horizontal",
        "min_width": 640,
        "per_page": 50,  # Fetch many to survive aggressive filtering
        "safesearch": "true",
    }

    try:
        async with session.get(PIXABAY_API_URL, params=params,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                logger.warning(f"Pixabay API {resp.status} for '{keyword}'")
                return None, None
            data = await resp.json()

        hits = data.get("hits", [])
        if not hits:
            logger.info(f"No images for '{keyword}'")
            return None, None

        # Filter: remove blacklisted and already-used images
        valid_hits = []
        for hit in hits:
            img_id = hit.get("id")

            # Skip already-used images
            if img_id in used_image_ids:
                logger.debug(f"Skipping already-used image ID {img_id}")
                continue

            # Skip blacklisted images for history content
            if _is_blacklisted_image(hit, is_history):
                continue

            valid_hits.append(hit)

        if not valid_hits:
            logger.info(f"No valid images after filtering for '{keyword}' "
                       f"(had {len(hits)} raw results)")
            return None, None

        # Score and sort valid hits (whitelist bonus integrated into scoring)
        scored = [(hit, _score_image_relevance(hit, keyword, document_topic, is_history))
                  for hit in valid_hits]
        scored.sort(key=lambda x: x[1], reverse=True)
        best_hit = scored[0][0]
        best_score = scored[0][1]
        best_id = best_hit.get("id")

        logger.debug(
            f"Best image for '{keyword}': id={best_id}, score={best_score}, "
            f"tags='{best_hit.get('tags', '')[:60]}'"
        )

        image_url = best_hit.get("webformatURL", "")
        if not image_url:
            return None, None

        # Use image ID in cache name to prevent collisions
        cache_name = f"{hashlib.md5(keyword.encode()).hexdigest()}_{best_id}.jpg"
        cache_path = settings.IMAGES_DIR / cache_name

        if cache_path.exists():
            logger.debug(f"Cache hit for '{keyword}' (id={best_id})")
            return cache_path, best_id

        async with session.get(image_url,
                               timeout=aiohttp.ClientTimeout(total=15)) as img_resp:
            if img_resp.status != 200:
                logger.warning(f"Image download failed: {img_resp.status}")
                return None, None

            settings.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
            image_data = await img_resp.read()
            cache_path.write_bytes(image_data)
            logger.info(f"Downloaded '{keyword}' → {cache_path.name} "
                       f"(id={best_id}, {len(image_data)} bytes, "
                       f"tags='{best_hit.get('tags', '')[:40]}')")
            return cache_path, best_id

    except Exception as e:
        logger.warning(f"Error fetching from Pixabay '{keyword}': {e}")
        return None, None


def _clean_keyword(keyword: str) -> str:
    """
    Sanitize a keyword for API search:
    - Strip non-alphanumeric chars (except spaces)
    - Collapse multiple spaces
    - Truncate to 100 chars
    - Lowercase
    """
    if not keyword:
        return keyword
    # Keep letters, digits, spaces only (including unicode letters)
    cleaned = re.sub(r'[^\w\s]', ' ', keyword, flags=re.UNICODE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    if len(cleaned) > 100:
        cleaned = cleaned[:100].rsplit(' ', 1)[0]  # truncate at word boundary
    return cleaned


def _is_junk_wikimedia_image(title: str, width: int, height: int) -> bool:
    """
    Filter out junk images from Wikimedia Commons:
    - Icons, logos, diagrams, maps, flags, badges
    - Very small or very tall/narrow images
    """
    t = title.lower()
    junk_patterns = [
        "icon", "logo", "badge", "seal", "coat of arms", "emblem",
        "flag of", "map of", "diagram", "chart", "graph", "symbol",
        ".svg", ".gif", "pictogram", "sign", "button", "banner ad",
        "stub", "commons-", "wikiproject", "category", "template",
    ]
    if any(p in t for p in junk_patterns):
        return True

    # Reject square icons and very narrow images
    if width < 500 and height < 500:
        return True
    aspect = width / max(height, 1)
    if aspect > 4.0 or aspect < 0.25:  # extreme aspect ratio
        return True

    return False


async def _wikimedia_api_call(
    session: aiohttp.ClientSession,
    params: dict,
    max_retries: int = 3,
) -> dict | None:
    """
    Make a Wikimedia API call with:
    - Semaphore concurrency limit
    - Random delay before each request
    - Retry with exponential backoff
    - Handle 429 (Too Many Requests)
    - Search result caching
    """
    # ── Cache check ──
    cache_key = hashlib.md5(str(sorted(params.items())).encode()).hexdigest()
    now = time.time()
    if cache_key in _search_cache:
        cached_time, cached_data = _search_cache[cache_key]
        if now - cached_time < _CACHE_TTL_SECONDS:
            logger.debug(f"Wikimedia cache hit for params hash {cache_key[:8]}")
            return cached_data

    # ── Semaphore + random delay ──
    async with _wikimedia_semaphore:
        for attempt in range(1, max_retries + 1):
            # Random delay: 1-3s (polite crawling)
            delay = random.uniform(1.0, 3.0)
            logger.debug(f"Wikimedia request: delay={delay:.1f}s, attempt={attempt}/{max_retries}")
            await asyncio.sleep(delay)

            try:
                async with session.get(
                    WIKIMEDIA_API_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers={"User-Agent": _USER_AGENT},
                ) as resp:
                    # ── Handle 429 ──
                    if resp.status == 429:
                        retry_after = resp.headers.get("Retry-After", "")
                        try:
                            wait_time = int(retry_after)
                        except (ValueError, TypeError):
                            wait_time = 10 * attempt  # fallback: escalating wait
                        logger.warning(
                            f"Wikimedia 429 rate limited. Retry-After={retry_after}. "
                            f"Waiting {wait_time}s (attempt {attempt}/{max_retries})"
                        )
                        await asyncio.sleep(wait_time)
                        continue

                    if resp.status != 200:
                        logger.warning(f"Wikimedia API {resp.status} (attempt {attempt}/{max_retries})")
                        if attempt < max_retries:
                            backoff = (2 ** attempt) + random.uniform(0, 1)
                            await asyncio.sleep(backoff)
                            continue
                        return None

                    data = await resp.json()

                    # ── Cache store ──
                    _search_cache[cache_key] = (now, data)
                    # Prune old cache entries (keep last 100)
                    if len(_search_cache) > 100:
                        oldest_keys = sorted(
                            _search_cache, key=lambda k: _search_cache[k][0]
                        )[:20]
                        for k in oldest_keys:
                            _search_cache.pop(k, None)

                    return data

            except asyncio.TimeoutError:
                logger.warning(f"Wikimedia timeout (attempt {attempt}/{max_retries})")
                if attempt < max_retries:
                    backoff = (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(backoff)
                    continue
                return None

            except Exception as e:
                logger.warning(f"Wikimedia request error: {e} (attempt {attempt}/{max_retries})")
                if attempt < max_retries:
                    backoff = (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(backoff)
                    continue
                return None

    return None


async def _search_wikimedia_commons(
    session: aiohttp.ClientSession,
    keyword: str,
    used_image_ids: set = None,
) -> tuple[Path | None, str | None]:
    """
    Search Wikimedia Commons for images matching the keyword.
    No API key required — uses the open MediaWiki API.
    Protected with semaphore, rate limiting, retry, caching.

    Returns:
        Tuple of (image_path, wikimedia_page_id_str) or (None, None).
    """
    if used_image_ids is None:
        used_image_ids = set()

    # ── Clean keyword ──
    cleaned_keyword = _clean_keyword(keyword)
    if not cleaned_keyword:
        logger.debug(f"Wikimedia: keyword '{keyword}' cleaned to empty, skipping")
        return None, None

    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": cleaned_keyword,
        "gsrnamespace": 6,       # File namespace only
        "gsrlimit": 15,          # Reduced from 30 to minimize server load
        "prop": "imageinfo",
        "iiprop": "url|size|mime",
        "iiurlwidth": 800,       # Request 800px thumbnail
        "format": "json",
    }

    # ── API call with retry/cache/semaphore ──
    data = await _wikimedia_api_call(session, params)
    if data is None:
        return None, None

    try:
        pages = data.get("query", {}).get("pages", {})
        if not pages:
            logger.info(f"No Wikimedia results for '{cleaned_keyword}'")
            return None, None

        # Filter and score results
        candidates = []
        for page_id, page in pages.items():
            # Unique ID for deduplication
            wm_id = f"wm_{page_id}"
            if wm_id in used_image_ids:
                continue

            info_list = page.get("imageinfo", [])
            if not info_list:
                continue
            info = info_list[0]

            # Only accept JPEG/PNG images
            mime = info.get("mime", "")
            if mime not in ("image/jpeg", "image/png"):
                logger.debug(f"Wikimedia skip non-image: {mime} — {page.get('title', '')[:50]}")
                continue

            width = info.get("width", 0)
            height = info.get("height", 0)
            title = page.get("title", "")

            # ── Filter junk images ──
            if _is_junk_wikimedia_image(title, width, height):
                logger.debug(f"Wikimedia skip junk: {title[:50]}")
                continue

            # Get thumbnail URL (resized) or fall back to original
            thumb_url = info.get("thumburl") or info.get("url", "")
            if not thumb_url:
                continue

            # Score: prefer horizontal, higher res
            score = 0
            if width > height:   # horizontal
                score += 10
            if width >= 1024:
                score += 3
            if width >= 1920:
                score += 2

            # Keyword match in title
            title_lower = title.lower()
            for word in cleaned_keyword.lower().split():
                if len(word) > 2 and word in title_lower:
                    score += 3

            candidates.append({
                "page_id": page_id,
                "wm_id": wm_id,
                "title": title,
                "thumb_url": thumb_url,
                "width": width,
                "height": height,
                "score": score,
            })

        if not candidates:
            logger.info(f"No valid Wikimedia images after filtering for '{cleaned_keyword}'")
            return None, None

        # Pick the best candidate
        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]

        logger.debug(
            f"Wikimedia best for '{cleaned_keyword}': score={best['score']}, "
            f"title='{best['title'][:60]}'"
        )

        # Download the thumbnail
        cache_name = f"wm_{hashlib.md5(cleaned_keyword.encode()).hexdigest()}_{best['page_id']}.jpg"
        cache_path = settings.IMAGES_DIR / cache_name

        if cache_path.exists():
            logger.debug(f"Wikimedia cache hit: '{cleaned_keyword}' (page={best['page_id']})")
            return cache_path, best["wm_id"]

        # ── Download with semaphore + random delay ──
        async with _wikimedia_semaphore:
            delay = random.uniform(0.5, 1.5)
            await asyncio.sleep(delay)

            try:
                async with session.get(
                    best["thumb_url"],
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers={"User-Agent": _USER_AGENT},
                ) as img_resp:
                    if img_resp.status != 200:
                        logger.warning(f"Wikimedia image download failed: {img_resp.status}")
                        return None, None

                    settings.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                    image_data = await img_resp.read()
                    cache_path.write_bytes(image_data)
                    logger.info(
                        f"Wikimedia downloaded '{cleaned_keyword}' → {cache_path.name} "
                        f"({len(image_data)} bytes, title='{best['title'][:50]}')"
                    )
                    return cache_path, best["wm_id"]
            except Exception as e:
                logger.warning(f"Wikimedia download error: {e}")
                return None, None

    except Exception as e:
        logger.warning(f"Error processing Wikimedia results '{cleaned_keyword}': {e}")
        return None, None


def _extract_fallback_keyword(slide_data: dict, document_topic: str = "") -> str:
    """Extract a smart fallback keyword from the slide title, using document topic for context."""
    title = slide_data.get("title", "")
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
    Fetch UNIQUE images for all slides, with deduplication and relevance filtering.
    Uses a cascading fallback strategy to ensure every slide gets an image.

    Args:
        slides: List of slide dicts with image_keyword field.
        document_topic: Overall document topic for keyword context and relevance scoring.
    """
    api_key = settings.PIXABAY_API_KEY
    if not api_key:
        logger.debug("No PIXABAY_API_KEY — Pixabay disabled, Wikimedia only")

    if document_topic:
        logger.info(f"Image search with document topic context: '{document_topic[:60]}'")

    is_vn_history = _is_vietnam_history_topic(document_topic)
    image_paths = {}
    used_image_ids = set()  # Track Pixabay image IDs to prevent duplicates
    used_keywords = set()   # Track keywords to detect LLM repeats
    fallback_pool_index = 0  # Rotating index into _VN_HISTORY_FALLBACK_KEYWORDS

    async with aiohttp.ClientSession() as session:
        for slide_data in slides:
            slide_num = slide_data.get("slide_number")
            keyword = slide_data.get("image_keyword", "").strip()
            found = False

            # ── Attempt 1: Wikimedia Commons (primary for VN history) ──
            if keyword and is_vn_history:
                enhanced_keyword = _enhance_keyword(keyword, document_topic)
                logger.info(f"Slide {slide_num}: trying Wikimedia for '{enhanced_keyword}'")
                img_path, img_id = await _search_wikimedia_commons(
                    session, enhanced_keyword, used_image_ids
                )
                if img_path and img_id:
                    image_paths[slide_num] = str(img_path)
                    used_image_ids.add(img_id)
                    found = True

                # Try original keyword if enhanced didn't work
                if not found and enhanced_keyword != keyword:
                    img_path, img_id = await _search_wikimedia_commons(
                        session, keyword, used_image_ids
                    )
                    if img_path and img_id:
                        image_paths[slide_num] = str(img_path)
                        used_image_ids.add(img_id)
                        found = True

            # ── Attempt 2: Pixabay (primary for non-history, fallback for history) ──
            if not found and keyword and api_key:
                if keyword.lower() in used_keywords:
                    logger.warning(f"Slide {slide_num}: DUPLICATE keyword '{keyword}'")
                used_keywords.add(keyword.lower())

                enhanced_keyword = _enhance_keyword(keyword, document_topic)
                logger.info(f"Slide {slide_num}: trying Pixabay for '{enhanced_keyword}'")

                img_path, img_id = await _search_and_download(
                    session, enhanced_keyword, api_key, document_topic, used_image_ids
                )
                if img_path and img_id:
                    image_paths[slide_num] = str(img_path)
                    used_image_ids.add(img_id)
                    found = True

                if not found and enhanced_keyword != keyword:
                    img_path, img_id = await _search_and_download(
                        session, keyword, api_key, document_topic, used_image_ids
                    )
                    if img_path and img_id:
                        image_paths[slide_num] = str(img_path)
                        used_image_ids.add(img_id)
                        found = True

            # ── Attempt 3: Fallback from slide title ──
            if not found:
                fallback = _extract_fallback_keyword(slide_data, document_topic)
                if fallback and fallback.lower() != (keyword or "").lower():
                    logger.info(f"Slide {slide_num}: title fallback '{fallback}'")
                    # Try Wikimedia first for history
                    if is_vn_history:
                        img_path, img_id = await _search_wikimedia_commons(
                            session, fallback, used_image_ids
                        )
                        if img_path and img_id:
                            image_paths[slide_num] = str(img_path)
                            used_image_ids.add(img_id)
                            found = True
                    # Then try Pixabay
                    if not found and api_key:
                        img_path, img_id = await _search_and_download(
                            session, fallback, api_key, document_topic, used_image_ids
                        )
                        if img_path and img_id:
                            image_paths[slide_num] = str(img_path)
                            used_image_ids.add(img_id)
                            found = True

            # ── Attempt 4: Curated safe fallback pool (VN history only) ──
            if not found and is_vn_history:
                attempts = 0
                while not found and attempts < 3:
                    safe_kw = _VN_HISTORY_FALLBACK_KEYWORDS[
                        fallback_pool_index % len(_VN_HISTORY_FALLBACK_KEYWORDS)
                    ]
                    fallback_pool_index += 1
                    attempts += 1

                    logger.info(f"Slide {slide_num}: safe fallback '{safe_kw}'")
                    # Try Wikimedia first
                    img_path, img_id = await _search_wikimedia_commons(
                        session, safe_kw, used_image_ids
                    )
                    if img_path and img_id:
                        image_paths[slide_num] = str(img_path)
                        used_image_ids.add(img_id)
                        found = True
                    # Then Pixabay
                    elif api_key:
                        img_path, img_id = await _search_and_download(
                            session, safe_kw, api_key, document_topic, used_image_ids
                        )
                        if img_path and img_id:
                            image_paths[slide_num] = str(img_path)
                            used_image_ids.add(img_id)
                            found = True

            if not found:
                logger.warning(f"Slide {slide_num}: NO image found after all fallback attempts")

            await asyncio.sleep(random.uniform(0.3, 0.8))  # Random delay between slides

    logger.info(f"Fetched {len(image_paths)} UNIQUE images for {len(slides)} slides "
               f"(used {len(used_image_ids)} distinct Pixabay IDs)")
    return image_paths
