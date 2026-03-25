"""
LLM Service — Google Gemini API integration (using google-genai SDK).
Replaces the OpenAI-based llm_ops.py for the API backend.

Developed by ChimSe (viduvan) - https://github.com/viduvan
"""
import json
import logging
import re

from google import genai
from google.genai import types

from ..core.config import settings
from .template_builder import AVAILABLE_THEMES

logger = logging.getLogger("odin_api.llm")

# ── Generation Progress Tracking ─────────────────────────────
_generation_progress: dict[str, dict] = {}


def get_generation_progress(session_id: str) -> dict | None:
    """Get current generation progress for a session."""
    return _generation_progress.get(session_id)


def clear_generation_progress(session_id: str):
    """Clear progress tracking for a session."""
    _generation_progress.pop(session_id, None)


def _get_client() -> genai.Client:
    """Get a configured Gemini client."""
    if not settings.GEMINI_API_KEY:
        raise ValueError(
            "GEMINI_API_KEY environment variable is not set. "
            "Please set it to your Google Gemini API key."
        )
    return genai.Client(api_key=settings.GEMINI_API_KEY)


def _extract_json_from_text(text: str) -> str | None:
    """Extract JSON array or object from LLM response text, with repair for truncated output."""
    # Try to find complete JSON array first
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        return match.group(0)

    # Try to repair truncated JSON array (output cut off before closing ])
    match = re.search(r'\[.*', text, re.DOTALL)
    if match:
        partial = match.group(0)
        # Find the last complete object (ends with })
        last_brace = partial.rfind('}')
        if last_brace > 0:
            repaired = partial[:last_brace + 1] + ']'
            try:
                json.loads(repaired)
                logger.warning("Repaired truncated JSON array")
                return repaired
            except json.JSONDecodeError:
                # Try removing the last incomplete object
                second_last = partial.rfind('}', 0, last_brace)
                if second_last > 0:
                    repaired = partial[:second_last + 1] + ']'
                    try:
                        json.loads(repaired)
                        logger.warning("Repaired truncated JSON (removed last incomplete object)")
                        return repaired
                    except json.JSONDecodeError:
                        pass

    # Fallback to JSON object
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return match.group(0)
    return None


async def summarize_content(text: str) -> str:
    """
    Summarize a piece of text using Gemini, optimized for slide generation.
    Includes automatic retry with backoff for rate limit (429) errors.

    Args:
        text: The text content to summarize.

    Returns:
        Summarized text string.
    """
    import asyncio

    client = _get_client()
    max_retries = 3

    input_word_count = len(text.split())
    target_word_count = int(input_word_count * 0.65)  # keep ~65% of content

    prompt = (
        "You are a document condenser. Your task is to shorten the text below "
        f"to approximately {target_word_count} words (currently {input_word_count} words). "
        "This is a LIGHT condensation, NOT a heavy summary.\n\n"
        "Rules:\n"
        "- Keep ALL key facts, data, statistics, names, and dates\n"
        "- Keep ALL main arguments, conclusions, and examples\n"
        "- Keep the original structure and section order\n"
        "- Only remove: redundant sentences, filler phrases, and verbose explanations\n"
        "- Do NOT reduce to bullet points or an outline\n"
        f"- Output MUST be approximately {target_word_count} words. Do NOT go below this.\n\n"
        "Output ONLY the condensed text, no commentary.\n\n"
        f"Text:\n{text}"
    )

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
            )
            result = response.text
            logger.debug(f"Summarization result: {len(text.split())} → {len(result.split())} words")
            return result
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                # Extract retry delay from error message if available
                retry_delay = 60  # default
                import re as _re
                delay_match = _re.search(r'retry in ([\d.]+)s', error_str, _re.IGNORECASE)
                if delay_match:
                    retry_delay = float(delay_match.group(1)) + 2  # add 2s buffer

                if attempt < max_retries:
                    logger.warning(
                        f"Rate limited (429), waiting {retry_delay:.0f}s "
                        f"before retry {attempt}/{max_retries}..."
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    logger.error(f"Rate limited after {max_retries} retries, giving up")
                    raise
            else:
                logger.error(f"Error during summarization: {e}")
                raise


def detect_theme_from_prompt(prompt: str) -> str:
    """
    Detect the best theme based on keywords in user prompt.

    Maps colors, topics, and moods to available theme presets.
    """
    p = prompt.lower()

    # Direct theme name match
    for theme in AVAILABLE_THEMES:
        if theme.replace("_", " ") in p or theme in p:
            return theme

    # Keyword → theme mapping
    theme_keywords = {
        "ocean": ["ocean", "sea", "water", "marine", "aqua", "blue", "teal",
                  "biển", "nước", "xanh dương", "xanh nước"],
        "forest": ["forest", "nature", "green", "eco", "environment", "plant", "tree",
                   "rừng", "thiên nhiên", "xanh lá", "cây", "môi trường"],
        "sunset": ["sunset", "warm", "orange", "fire", "energy", "autumn",
                   "hoàng hôn", "cam", "ấm", "năng lượng", "lửa"],
        "midnight": ["tech", "technology", "digital", "ai", "data", "software", "code",
                     "cyber", "cloud", "server", "database", "engineering", "dev",
                     "công nghệ", "phần mềm", "kỹ thuật", "lập trình", "dữ liệu"],
        "crimson": ["medical", "health", "heart", "blood", "emergency", "passion",
                    "red", "danger", "y tế", "sức khỏe", "đỏ", "y khoa"],
        "emerald_gold": ["finance", "business", "money", "gold", "luxury", "premium",
                         "wealth", "investment", "kinh doanh", "tài chính", "vàng", "sang trọng"],
        "rose": ["fashion", "beauty", "design", "art", "creative", "music", "love",
                 "pink", "thời trang", "nghệ thuật", "thiết kế", "sáng tạo", "hồng"],
        "dark_purple": ["space", "universe", "galaxy", "science", "research", "education",
                        "vũ trụ", "khoa học", "giáo dục", "nghiên cứu"],
    }

    # Score each theme
    best_theme = "midnight"  # Default to midnight (good general tech/modern look)
    best_score = 0

    for theme, keywords in theme_keywords.items():
        score = sum(1 for kw in keywords if kw in p)
        if score > best_score:
            best_score = score
            best_theme = theme

    return best_theme


def _extract_document_topic(text: str, max_words: int = 200) -> str:
    """Extract a short topic phrase from document content for image keyword guidance."""
    if not text:
        return ""
    preview = " ".join(text.split()[:max_words])

    # Try to identify key subjects from the beginning of the document
    # Common patterns: titles, headings, first paragraph
    lines = preview.split("\n")
    # Take the first non-empty line as potential title/subject
    subject_line = ""
    for line in lines:
        stripped = line.strip()
        if stripped and len(stripped) > 3:
            subject_line = stripped[:100]  # cap at 100 chars
            break

    return subject_line


# Vietnamese history keywords for detection (~200+ keywords)
_VIETNAM_HISTORY_KEYWORDS = {
    # ═══ HISTORICAL FIGURES ═══════════════════════════════════
    # Founding & Legendary
    "hùng vương", "vua hùng", "lạc long quân", "âu cơ", "an dương vương",
    "sơn tinh", "thủy tinh", "thánh gióng",
    # Bắc thuộc & Khởi nghĩa
    "hai bà trưng", "trưng trắc", "trưng nhị", "bà triệu", "triệu thị trinh",
    "lý bí", "lý nam đế", "triệu quang phục", "mai thúc loan", "mai hắc đế",
    "phùng hưng", "bố cái đại vương", "khúc thừa dụ", "ngô quyền",
    "dương đình nghệ",
    # Nhà Đinh, Tiền Lê, Lý
    "đinh bộ lĩnh", "đinh tiên hoàng", "lê hoàn", "lê đại hành",
    "lý thái tổ", "lý công uẩn", "lý thường kiệt", "lý thánh tông",
    "lý nhân tông", "lý thái tông",
    # Nhà Trần
    "trần hưng đạo", "trần quốc tuấn", "trần thái tông", "trần nhân tông",
    "trần quốc toản", "trần khánh dư", "phạm ngũ lão", "yết kiêu",
    "trần quang khải", "trần thủ độ", "trần bình trọng",
    # Nhà Hồ, Hậu Trần, Lê
    "hồ quý ly", "lê lợi", "lê thái tổ", "nguyễn trãi",
    "lê thánh tông", "lê nhân tông", "ngô sĩ liên",
    # Tây Sơn & Nguyễn
    "quang trung", "nguyễn huệ", "nguyễn nhạc", "nguyễn lữ",
    "gia long", "nguyễn ánh", "minh mạng", "tự đức",
    "nguyễn du", "nguyễn đình chiểu", "nguyễn công trứ",
    # Cận đại - Chống Pháp
    "phan bội châu", "phan châu trinh", "phan đình phùng",
    "hoàng hoa thám", "đề thám", "nguyễn thái học", "phạm hồng thái",
    "trương định", "nguyễn trung trực", "tôn thất thuyết",
    "lương văn can", "trần cao vân", "thái phiên",
    # Cách mạng & Kháng chiến
    "hồ chí minh", "ho chi minh", "bác hồ", "nguyễn ái quốc", "nguyễn sinh cung",
    "nguyễn tất thành", "võ nguyên giáp", "phạm văn đồng", "trường chinh",
    "lê duẩn", "tôn đức thắng", "hoàng văn thụ", "nguyễn văn cừ",
    "trần phú", "lê hồng phong", "hà huy tập", "nguyễn thị minh khai",
    "võ thị sáu", "nguyễn văn trỗi", "lý tự trọng", "kim đồng",
    "la văn cầu", "phan đình giót", "tô vĩnh diện", "bế văn đàn",
    "nguyễn viết xuân", "cù chính lan", "nguyễn chí thanh",
    "hoàng minh thảo", "văn tiến dũng", "lê trọng tấn",
    "trần văn trà", "nguyễn hữu an", "đồng sĩ nguyên",
    "nguyễn thị bình", "đặng thùy trâm", "nguyễn văn bé",

    # ═══ EVENTS & BATTLES ═════════════════════════════════════
    # Cổ đại & Trung đại
    "trận bạch đằng", "bạch đằng", "sông bạch đằng",
    "trận chi lăng", "chi lăng", "trận đống đa", "đống đa",
    "trận ngọc hồi", "trận rạch gầm", "rạch gầm xoài mút",
    "trận như nguyệt", "trận bình lệ nguyên",
    "khởi nghĩa lam sơn", "hội nghị diên hồng",
    "chiếu dời đô", "nam quốc sơn hà",
    # Chống Pháp
    "kháng chiến chống pháp", "chống pháp",
    "phong trào cần vương", "cần vương", "đông du",
    "phong trào đông kinh nghĩa thục", "đông kinh nghĩa thục",
    "khởi nghĩa yên bái", "yên bái",
    "đảng cộng sản", "thành lập đảng", "xô viết nghệ tĩnh",
    # Cách mạng & Kháng chiến chống Pháp hiện đại
    "cách mạng tháng tám", "cách mạng tháng 8",
    "tuyên ngôn độc lập", "2 tháng 9", "quốc khánh",
    "toàn quốc kháng chiến", "thu đông", "chiến dịch biên giới",
    "điện biên phủ", "dien bien phu", "trận điện biên phủ",
    "hiệp định geneva", "genève",
    # Kháng chiến chống Mỹ
    "chống mỹ", "kháng chiến chống mỹ", "đế quốc mỹ",
    "tết mậu thân", "mậu thân 1968",
    "chiến dịch hồ chí minh", "đường trường sơn", "trường sơn",
    "đường mòn hồ chí minh", "tổng tiến công",
    "30 tháng 4", "giải phóng miền nam", "giải phóng sài gòn",
    "thống nhất đất nước", "hiệp định paris",
    "chiến thắng mùa xuân", "đại thắng mùa xuân",
    "trận khe sanh", "khe sanh", "ấp bắc", "trận ấp bắc",
    "vạn tường", "chiến dịch tây nguyên",
    "phước long", "buôn ma thuột", "đà nẵng",
    "dinh độc lập", "xe tăng húc đổ cổng",
    # Hải chiến
    "hoàng sa", "trường sa", "hải chiến hoàng sa",

    # ═══ DYNASTIES & PERIODS ══════════════════════════════════
    "nhà trần", "nhà lý", "nhà lê", "nhà nguyễn", "nhà hồ",
    "nhà hậu lê", "nhà đinh", "tiền lê", "nhà mạc",
    "nhà tây sơn", "tây sơn",
    "đời trần", "đời lý", "đời lê",
    "triều đại", "triều nguyễn", "triều lê", "triều lý", "triều trần",
    "phong kiến", "thời kỳ bắc thuộc", "bắc thuộc",
    "thời kỳ tự chủ", "đại việt", "đại cồ việt", "văn lang", "âu lạc",
    "nam việt", "chăm pa", "champa", "phù nam",
    "đàng trong", "đàng ngoài", "chúa trịnh", "chúa nguyễn",
    "lịch sử việt nam", "lịch sử vn",

    # ═══ PLACES & LANDMARKS ═══════════════════════════════════
    "ba đình", "quảng trường ba đình", "lăng bác", "lăng chủ tịch",
    "hoàng thành thăng long", "thăng long", "kẻ chợ",
    "cố đô huế", "đại nội huế", "kinh thành huế",
    "đền hùng", "phú thọ", "côn đảo", "hỏa lò",
    "phủ chủ tịch", "nhà sàn bác hồ",
    "địa đạo củ chi", "củ chi", "bến nhà rồng",
    "hang pác bó", "pác bó", "tân trào",
    "điện biên", "mường phăng",
    "thành cổ quảng trị", "quảng trị",
    "ngã ba đồng lộc", "đồng lộc",
    "cầu hiền lương", "vĩ tuyến 17",
    "cổ loa", "thành cổ loa",
    "hoa lư", "kinh đô hoa lư",
    "vịnh hạ long", "mỹ sơn", "phố cổ hội an",
    "văn miếu quốc tử giám", "văn miếu",

    # ═══ CONCEPTS & TERMS ═════════════════════════════════════
    "chiến dịch", "kháng chiến", "giải phóng", "độc lập",
    "thống nhất", "cách mạng", "khởi nghĩa",
    "chiến thắng", "đấu tranh", "yêu nước",
    "thực dân pháp", "thực dân", "đế quốc",
    "phong trào", "quốc gia", "dân tộc",
    "anh hùng", "liệt sĩ", "tử sĩ", "nghĩa quân",
    "dân công", "bộ đội", "quân đội nhân dân",
    "đoàn thanh niên", "hội phụ nữ",
    "cải cách ruộng đất", "hợp tác xã",
    "đổi mới", "xây dựng chủ nghĩa xã hội",
    "chống quân nguyên", "chống mông",
    "chống quân thanh", "chống quân minh",
    "nam tiến", "mở cõi",
}



# Vietnamese LITERATURE keywords for detection
_VIETNAM_LITERATURE_KEYWORDS = {
    # ═══ AUTHORS & POETS ══════════════════════════════════════
    # Classical
    "nguyễn du", "nguyễn trãi", "hồ xuân hương", "bà huyện thanh quan",
    "nguyễn đình chiểu", "nguyễn công trứ", "cao bá quát", "đoàn thị điểm",
    "lê quý đôn", "nguyễn bỉnh khiêm", "trạng trình",
    "phạm đình hổ", "lê hữu trác", "hải thượng lãn ông",
    "đặng trần côn", "ngô thì nhậm",
    # Modern (early 20th century)
    "nam cao", "ngô tất tố", "vũ trọng phụng", "nguyên hồng",
    "thạch lam", "xuân diệu", "huy cận", "chế lan viên",
    "hàn mặc tử", "tố hữu", "nguyễn tuân", "tô hoài",
    "nhất linh", "khái hưng", "thế lữ", "lưu trọng lư",
    "phạm quỳnh", "nguyễn văn vĩnh", "phan khôi",
    "tản đà", "trần tế xương", "tú xương",
    "nguyễn khuyến", "nguyễn khắc hiếu",
    # Modern & Contemporary
    "nguyễn minh châu", "lê minh khuê", "bảo ninh", "nguyễn huy thiệp",
    "dương thu hương", "ma văn kháng", "nguyễn nhật ánh",
    "nguyễn ngọc tư", "phạm tiến duật", "thu bồn",
    "anh đức", "nguyễn quang sáng", "sơn nam",
    "hồ chí minh",  # as poet/writer (Nhật ký trong tù)
    "trần đăng khoa", "phạm thị hoài",
    # War-era writers
    "dương thị xuân quý", "lê anh xuân", "nguyễn thi",

    # ═══ LITERARY WORKS ═══════════════════════════════════════
    # Classical
    "truyện kiều", "kiều", "đoạn trường tân thanh",
    "chinh phụ ngâm", "cung oán ngâm khúc", "cung oán ngâm",
    "lục vân tiên", "văn tế nghĩa sĩ cần giuộc",
    "bình ngô đại cáo", "hịch tướng sĩ",
    "quốc âm thi tập", "truyền kỳ mạn lục", "hoàng lê nhất thống chí",
    "thượng kinh ký sự", "vũ trung tùy bút",
    "nam quốc sơn hà", "chiếu dời đô",
    "truyện an dương vương", "sự tích trầu cau",
    "tấm cám", "sơn tinh thủy tinh", "thánh gióng",
    # Modern works
    "chí phèo", "tắt đèn", "lão hạc", "số đỏ", "giông tố",
    "vợ nhặt", "vợ chồng a phủ", "đời thừa",
    "bước đường cùng", "bỉ vỏ", "dế mèn phiêu lưu ký",
    "tây tiến", "việt bắc", "đất nước",
    "nhật ký trong tù", "tuyên ngôn độc lập",
    "rừng xà nu", "những ngôi sao xa xôi",
    "nỗi buồn chiến tranh", "mắt biếc",
    "tôi thấy hoa vàng trên cỏ xanh", "cho tôi xin một vé đi tuổi thơ",
    "kính vạn hoa", "đất rừng phương nam",
    "mùa lá rụng trong vườn", "thời xa vắng",

    # ═══ LITERARY CONCEPTS & GENRES ═══════════════════════════
    "văn học việt nam", "văn học vn", "văn học",
    "thơ", "truyện ngắn", "tiểu thuyết", "kịch",
    "ca dao", "tục ngữ", "thành ngữ", "câu đố",
    "truyện cổ tích", "truyện truyền thuyết", "truyền thuyết",
    "sử thi", "hịch", "cáo", "chiếu", "biểu",
    "thơ lục bát", "lục bát", "song thất lục bát",
    "thơ đường luật", "thơ tứ tuyệt", "ngũ ngôn",
    "chữ nôm", "chữ hán", "quốc ngữ",
    "thơ mới", "phong trào thơ mới", "tự lực văn đoàn",
    "văn học hiện thực", "hiện thực phê phán",
    "văn học cách mạng", "văn học kháng chiến",
    "văn học trung đại", "văn học dân gian",
    "văn xuôi", "phóng sự", "bút ký", "tùy bút", "hồi ký",
    "nhà thơ", "nhà văn", "thi sĩ", "tác phẩm", "tác giả",
    "phân tích", "bình giảng", "cảm nhận", "nghệ thuật",
}

# Vietnamese GEOGRAPHY keywords for detection
_VIETNAM_GEOGRAPHY_KEYWORDS = {
    # ═══ REGIONS & AREAS ══════════════════════════════════════
    "việt nam", "bắc bộ", "trung bộ", "nam bộ",
    "tây bắc", "đông bắc", "bắc trung bộ", "nam trung bộ",
    "tây nguyên", "đông nam bộ", "tây nam bộ",
    "đồng bằng sông hồng", "đồng bằng sông cửu long",
    "duyên hải miền trung",

    # ═══ PROVINCES & CITIES ═══════════════════════════════════
    "hà nội", "hồ chí minh", "sài gòn", "đà nẵng", "hải phòng", "cần thơ",
    "huế", "nha trang", "đà lạt", "vũng tàu", "quy nhơn", "hạ long",
    "phú quốc", "hội an", "sapa", "sa pa", "tam đảo", "ba vì",
    "hà giang", "cao bằng", "lạng sơn", "lào cai", "yên bái",
    "thái nguyên", "bắc kạn", "tuyên quang", "phú thọ",
    "sơn la", "điện biên", "lai châu", "hòa bình",
    "quảng ninh", "bắc giang", "bắc ninh", "hải dương",
    "hưng yên", "thái bình", "nam định", "ninh bình",
    "hà nam", "vĩnh phúc",
    "thanh hóa", "nghệ an", "hà tĩnh", "quảng bình",
    "quảng trị", "thừa thiên huế",
    "quảng nam", "quảng ngãi", "bình định", "phú yên",
    "khánh hòa", "ninh thuận", "bình thuận",
    "kon tum", "gia lai", "đắk lắk", "đắk nông", "lâm đồng",
    "bình phước", "tây ninh", "bình dương", "đồng nai",
    "bà rịa", "long an", "tiền giang", "bến tre",
    "trà vinh", "vĩnh long", "đồng tháp", "an giang",
    "kiên giang", "hậu giang", "sóc trăng", "bạc liêu", "cà mau",

    # ═══ RIVERS ═══════════════════════════════════════════════
    "sông hồng", "sông mê kông", "mekong", "sông cửu long",
    "sông đà", "sông lô", "sông mã", "sông cả", "sông lam",
    "sông hương", "sông thu bồn", "sông đồng nai",
    "sông bạch đằng", "sông tiền", "sông hậu",
    "sông bến hải", "sông thạch hãn",

    # ═══ MOUNTAINS & HIGHLANDS ════════════════════════════════
    "fansipan", "phan xi păng", "hoàng liên sơn",
    "dãy trường sơn", "trường sơn", "tây nguyên",
    "núi bà đen", "núi ngự bình", "núi bà nà",
    "núi cấm", "tam đảo", "ba vì", "yên tử",
    "đèo hải vân", "hải vân", "đèo ngang",
    "đèo khau phạ", "đèo mã pí lèng", "mã pí lèng",
    "cao nguyên", "đồi chè",

    # ═══ SEAS, ISLANDS & COASTAL ══════════════════════════════
    "biển đông", "vịnh hạ long", "vịnh bắc bộ", "vịnh thái lan",
    "côn đảo", "phú quốc", "cát bà", "lý sơn",
    "hoàng sa", "trường sa", "bán đảo sơn trà",
    "bãi biển", "bờ biển", "đường bờ biển",
    "mũi né", "mũi cà mau", "mũi đại lãnh",

    # ═══ NATURAL FEATURES & HERITAGE ══════════════════════════
    "phong nha kẻ bàng", "phong nha", "kẻ bàng",
    "tràng an", "tam cốc", "bích động",
    "rừng ngập mặn", "đất ngập nước", "rừng nhiệt đới",
    "vườn quốc gia", "khu bảo tồn", "di sản thế giới",
    "cúc phương", "cát tiên", "ba bể", "hồ ba bể",
    "thác bản giốc", "ruộng bậc thang",
    "hồ hoàn kiếm", "hồ tây", "hồ xuân hương",

    # ═══ GEOGRAPHY CONCEPTS ═══════════════════════════════════
    "địa lý việt nam", "địa lí việt nam", "địa lý", "địa lí",
    "địa hình", "khí hậu", "nhiệt đới", "gió mùa",
    "đồng bằng", "châu thổ", "bồi tụ", "phù sa",
    "vùng kinh tế", "dân cư", "dân số",
    "nông nghiệp", "lúa nước", "lúa gạo",
    "biên giới", "lãnh thổ", "diện tích",
    "hình chữ s", "bản đồ", "tọa độ",
    "tỉnh", "thành phố", "quận", "huyện",
    "miền bắc", "miền trung", "miền nam",
}


def _detect_document_context(text: str) -> dict:
    """
    Detect document topics: Vietnamese history, literature, geography.
    Returns a dict with detected context flags.
    """
    if not text:
        return {
            "is_history": False, "is_vietnam_history": False,
            "is_literature": False, "is_vietnam_literature": False,
            "is_geography": False, "is_vietnam_geography": False,
            "matched": [],
        }

    text_lower = text.lower()
    preview = text_lower[:5000]  # Check first 5000 chars

    # ── Vietnamese History ──
    matched_vn_history = [kw for kw in _VIETNAM_HISTORY_KEYWORDS if kw in preview]
    general_history_kw = {
        "lịch sử", "history", "historical", "tiểu sử", "biography",
        "cuộc đời", "thế kỷ", "century", "triều đại", "dynasty",
        "chiến tranh", "war", "cách mạng", "revolution",
    }
    matched_gen_history = [kw for kw in general_history_kw if kw in preview]
    is_vn_history = len(matched_vn_history) >= 2 or (
        len(matched_vn_history) >= 1 and len(matched_gen_history) >= 1
    )
    is_history = is_vn_history or len(matched_gen_history) >= 2

    # ── Vietnamese Literature ──
    matched_vn_lit = [kw for kw in _VIETNAM_LITERATURE_KEYWORDS if kw in preview]
    general_lit_kw = {
        "văn học", "literature", "literary", "thơ", "poetry", "poem",
        "truyện", "novel", "story", "tác phẩm", "nhà văn", "nhà thơ",
        "phân tích", "bình giảng", "cảm nhận", "tác giả", "author",
    }
    matched_gen_lit = [kw for kw in general_lit_kw if kw in preview]
    is_vn_literature = len(matched_vn_lit) >= 2 or (
        len(matched_vn_lit) >= 1 and len(matched_gen_lit) >= 1
    )
    is_literature = is_vn_literature or len(matched_gen_lit) >= 2

    # ── Vietnamese Geography ──
    matched_vn_geo = [kw for kw in _VIETNAM_GEOGRAPHY_KEYWORDS if kw in preview]
    general_geo_kw = {
        "địa lý", "địa lí", "geography", "geographical",
        "địa hình", "terrain", "khí hậu", "climate",
        "sông", "river", "núi", "mountain", "biển", "sea",
        "đồng bằng", "plain", "vùng", "region",
    }
    matched_gen_geo = [kw for kw in general_geo_kw if kw in preview]
    is_vn_geography = len(matched_vn_geo) >= 3 or (
        len(matched_vn_geo) >= 2 and len(matched_gen_geo) >= 1
    )
    is_geography = is_vn_geography or len(matched_gen_geo) >= 3

    # Build result
    all_matched = matched_vn_history[:3] + matched_vn_lit[:3] + matched_vn_geo[:3]
    result = {
        "is_history": is_history,
        "is_vietnam_history": is_vn_history,
        "is_literature": is_literature,
        "is_vietnam_literature": is_vn_literature,
        "is_geography": is_geography,
        "is_vietnam_geography": is_vn_geography,
        "matched": all_matched[:5],
    }

    # Logging
    detected = []
    if is_vn_history:
        detected.append(f"VN History ({matched_vn_history[:3]})")
    elif is_history:
        detected.append(f"History ({matched_gen_history[:3]})")
    if is_vn_literature:
        detected.append(f"VN Literature ({matched_vn_lit[:3]})")
    elif is_literature:
        detected.append(f"Literature ({matched_gen_lit[:3]})")
    if is_vn_geography:
        detected.append(f"VN Geography ({matched_vn_geo[:3]})")
    elif is_geography:
        detected.append(f"Geography ({matched_gen_geo[:3]})")

    if detected:
        logger.info(f"Document context detected: {', '.join(detected)}")

    return result


async def generate_slides(
    prompt: str,
    word_content: str = "",
    existing_slides: list[dict] | None = None,
    skip_slide_count: bool = False,
) -> dict:
    """
    Generate or update slide content using Gemini.

    Args:
        prompt: User's instruction for slide creation/editing.
        word_content: Optional document content to base slides on.
        existing_slides: Optional existing slides for editing.

    Returns:
        Dict with 'slides', 'theme', and 'document_topic'.
    """
    client = _get_client()

    if existing_slides is None:
        existing_slides = []

    # Reset narration to default for existing slides
    for slide in existing_slides:
        slide["narration"] = ""

    # Safety: truncate word_content if still too large
    if word_content:
        wc = len(word_content.split())
        if wc > settings.MAX_CONTENT_FOR_LLM:
            logger.warning(
                f"word_content is {wc} words, truncating to {settings.MAX_CONTENT_FOR_LLM} "
                f"before sending to LLM"
            )
            words = word_content.split()
            word_content = " ".join(words[:settings.MAX_CONTENT_FOR_LLM])

    # Extract document topic for image keyword guidance
    document_topic = _extract_document_topic(word_content) if word_content else ""
    doc_context = _detect_document_context(word_content) if word_content else {
        "is_history": False, "is_vietnam_history": False,
        "is_literature": False, "is_vietnam_literature": False,
        "is_geography": False, "is_vietnam_geography": False,
        "matched": [],
    }
    if document_topic:
        logger.info(f"Document topic extracted: '{document_topic[:80]}...'")

    # Build the system instruction (formatting rules only, NO article content)
    system_parts = []

    # Calculate recommended slide count based on content size
    slide_count_instruction = ""
    if word_content and not existing_slides and not skip_slide_count:
        wc = len(word_content.split())
        if wc <= 2000:
            recommended_slides = 8
        elif wc <= 10000:
            recommended_slides = 12
        elif wc <= 30000:
            recommended_slides = 16
        elif wc <= 60000:
            recommended_slides = 20
        else:
            recommended_slides = 25

        slide_count_instruction = (
            f'\n\nIMPORTANT: The input article has {wc} words. '
            f'You MUST create at least {recommended_slides} slides to adequately cover ALL the content. '
            f'Cover every major section, chapter, or topic in the article. '
            f'Do NOT summarize the entire article into just 2-3 overview slides. '
            f'Each major section should have its own slide(s).'
        )

    slide_format_instruction = (
        'You create presentation slides'
        + (' based on the article provided by the user' if word_content else '')
        + '. The response format should be a valid json format structured as this: '
        '[{"slide_number": <Float>, "title": "<String>", "content": "<String>", "narration": "<String>", "image_keyword": "<String>"},'
        '{"slide_number": <Float>, "title": "<String>", "content": "<String>", "narration": "<String>", "image_keyword": "<String>"}]\n'
        '\nCONTENT DENSITY REQUIREMENT (CRITICAL):\n'
        'Each slide MUST have 5-8 bullet points in the content field. '
        'Each bullet point must be a complete, informative sentence (15-30 words). '
        'A slide with fewer than 5 bullet points is UNACCEPTABLE.\n'
        'Example of good slide content:\n'
        '"- The Vietnam War lasted from 1955 to 1975, involving North Vietnam and South Vietnam.\\n'
        '- The United States committed over 500,000 troops at the peak of its involvement in 1968.\\n'
        '- The Tet Offensive in January 1968 marked a major turning point in public opinion.\\n'
        '- Over 58,000 American soldiers and 3 million Vietnamese lost their lives.\\n'
        '- The Paris Peace Accords were signed in January 1973, leading to US withdrawal.\\n'
        '- Saigon fell on April 30, 1975, reunifying Vietnam under communist rule."\n'
        '\nFor content use bullet points with dash (-) prefix.\n'
        'CRITICAL: Do NOT use any HTML tags or markdown formatting. Use ONLY plain text.\n'
        'If you are modifying an existing slide leave the slide number unchanged '
        'but if you are adding slides, use decimal digits for the slide number. '
        'For example to add a slide after slide 2, use slide number 2.1, 2.2, ...\n'
        'If user asks to remove a slide, set its slide number to negative of its current value.\n'
        + slide_count_instruction + '\n'
        f'The existing slides are as follows: {json.dumps(existing_slides)}'
    )
    system_parts.append(slide_format_instruction)

    # Build image keyword instruction with document-specific context
    image_keyword_instruction = (
        "LANGUAGE RULE: The title and content of each slide MUST be written in the SAME language "
        "as the input article. If the article is in Vietnamese, write slides in Vietnamese. "
        "If the article is in English, write slides in English. "
        "ONLY the image_keyword field should always be in English.\n\n"
        "The narration field should be left empty unless explicitly requested.\n\n"
    )

    # Add topic-aware image keyword rules
    image_keyword_instruction += (
        "IMAGE_KEYWORD RULES (CRITICAL - READ CAREFULLY):\n"
        "The image_keyword field is used to search stock photos on Pixabay. "
        "BAD keywords will result in COMPLETELY WRONG images on the slides.\n\n"
        "Rules:\n"
        "- Each image_keyword MUST be 2-4 specific English words directly related to the slide content\n"
        "- MUST include the main subject/person/topic name when applicable\n"
        "- Be VERY SPECIFIC - generic words return random unrelated photos\n"
    )

    # Add document-specific context if available
    if document_topic:
        image_keyword_instruction += (
            f"\nDOCUMENT CONTEXT: This document is about: \"{document_topic}\"\n"
            "ALL image_keyword values MUST be relevant to this specific topic.\n"
            "Include the main subject name in most image_keyword values.\n\n"
        )

    image_keyword_instruction += (
        "Format: \"{main subject} {specific detail}\"\n"
        "Examples of GOOD vs BAD image_keyword values:\n"
        "  For a document about Ho Chi Minh:\n"
        "    GOOD: \"Ho Chi Minh\", \"Vietnam independence\", \"Hanoi Vietnam\", \"Vietnam revolution\"\n"
        "    BAD: \"leader\", \"man\", \"flag\", \"woman\", \"field\", \"soldier\"\n"
        "  For a document about Climate Change:\n"
        "    GOOD: \"global warming earth\", \"carbon emission factory\", \"solar energy panel\"\n"
        "    BAD: \"nature\", \"world\", \"green\", \"sky\"\n"
        "  For a document about Machine Learning:\n"
        "    GOOD: \"artificial intelligence brain\", \"neural network diagram\", \"data analysis chart\"\n"
        "    BAD: \"computer\", \"technology\", \"screen\"\n\n"
        "FORBIDDEN - NEVER use these generic words alone as image_keyword:\n"
        "person, man, woman, people, girl, boy, landscape, nature, building, city, \n"
        "field, flag, leader, soldier, office, team, sky, road, mountain\n\n"
    )

    # Add STRICT Vietnamese history rules if detected
    if doc_context.get("is_vietnam_history"):
        image_keyword_instruction += (
            "⚠️ VIETNAMESE HISTORY DOCUMENT DETECTED — SPECIAL RULES APPLY:\n"
            "This document is about VIETNAMESE HISTORY. Image keywords MUST be strictly relevant.\n\n"
            "MANDATORY: Every image_keyword MUST include 'Vietnam' or a specific Vietnamese \n"
            "historical name/place/event. Examples:\n"
            "  \"Vietnam Ho Chi Minh\", \"Dien Bien Phu battle\", \"Vietnam independence ceremony\",\n"
            "  \"Ba Dinh square Hanoi\", \"Vietnam temple heritage\", \"Hanoi old quarter\",\n"
            "  \"Vietnam rice field countryside\", \"Hue imperial citadel\", \"Vietnam war memorial\",\n"
            "  \"Vietnam traditional culture\", \"Thang Long Hanoi\", \"Vietnam pagoda temple\",\n"
            "  \"Vo Nguyen Giap\", \"Vietnam revolution poster\", \"Vietnam flag red star\"\n\n"
            "ABSOLUTELY FORBIDDEN for Vietnamese history documents:\n"
            "- Random women/girls/models (NEVER appropriate for history content)\n"
            "- American soldiers or US military imagery\n"
            "- South Vietnam flag (yellow with red stripes) — this is politically sensitive\n"
            "- Generic landscape/nature photos without Vietnamese context\n"
            "- Any image that could be disrespectful to Vietnamese historical figures\n"
            "- Modern city photos that are not Vietnamese\n"
            "- Random flags from other countries\n\n"
        )
    elif doc_context.get("is_history"):
        image_keyword_instruction += (
            "HISTORY DOCUMENT DETECTED — Image keywords must be historically relevant.\n"
            "Always include the specific historical period, person, or event name.\n"
            "NEVER use generic keywords like 'leader', 'war', 'battle' without context.\n"
            "Always prefix with the specific country/era: e.g., 'Vietnam revolution', not just 'revolution'.\n\n"
        )

    # Add Vietnamese LITERATURE rules if detected
    if doc_context.get("is_vietnam_literature"):
        image_keyword_instruction += (
            "📚 VIETNAMESE LITERATURE DOCUMENT DETECTED — SPECIAL RULES APPLY:\n"
            "This document is about VIETNAMESE LITERATURE. Image keywords MUST reflect \n"
            "Vietnamese literary culture, NOT random unrelated photos.\n\n"
            "MANDATORY: Every image_keyword MUST be related to Vietnamese culture, literature, \n"
            "or the specific literary work/author. Examples:\n"
            "  \"Vietnam traditional poetry\", \"Vietnamese calligraphy art\",\n"
            "  \"Vietnam ancient book scroll\", \"Vietnamese village countryside\",\n"
            "  \"Vietnam temple literature\", \"Vietnamese traditional painting\",\n"
            "  \"Vietnam old scholar\", \"Vietnamese ink brush\",\n"
            "  \"Vietnam Hanoi temple literature\", \"Vietnamese woman ao dai\",\n"
            "  \"Vietnam rice paddy landscape\", \"Vietnamese folk art\"\n\n"
            "For specific works, use the work's theme:\n"
            "  Truyện Kiều → \"Vietnamese woman traditional dress\", \"Vietnam moonlight poetry\"\n"
            "  Chí Phèo → \"Vietnamese village rural\", \"Vietnam peasant countryside\"\n"
            "  Tắt Đèn → \"Vietnamese poor family village\", \"Vietnam rural hardship\"\n\n"
            "FORBIDDEN for Vietnamese literature documents:\n"
            "- Modern Western/non-Vietnamese imagery\n"
            "- Random photos of people not in Vietnamese context\n"
            "- Generic technology, business, or office imagery\n\n"
        )
    elif doc_context.get("is_literature"):
        image_keyword_instruction += (
            "LITERATURE DOCUMENT DETECTED — Image keywords must reflect literary themes.\n"
            "Use keywords related to books, writing, cultural context of the literary work.\n"
            "NEVER use generic keywords unrelated to literature or the work's setting.\n\n"
        )

    # Add Vietnamese GEOGRAPHY rules if detected
    if doc_context.get("is_vietnam_geography"):
        image_keyword_instruction += (
            "🗺️ VIETNAMESE GEOGRAPHY DOCUMENT DETECTED — SPECIAL RULES APPLY:\n"
            "This document is about VIETNAMESE GEOGRAPHY. Image keywords MUST show \n"
            "actual Vietnamese landscapes, maps, and locations.\n\n"
            "MANDATORY: Every image_keyword MUST include 'Vietnam' or a specific Vietnamese \n"
            "geographic feature/location. Examples:\n"
            "  \"Vietnam Ha Long Bay\", \"Vietnam rice terrace Sapa\",\n"
            "  \"Vietnam Mekong Delta river\", \"Vietnam Da Lat highlands\",\n"
            "  \"Vietnam map geography\", \"Vietnam Phong Nha cave\",\n"
            "  \"Vietnam Ho Chi Minh City aerial\", \"Vietnam Hanoi Red River\",\n"
            "  \"Vietnam Hue Perfume River\", \"Vietnam central highlands coffee\",\n"
            "  \"Vietnam coastline beach\", \"Vietnam Fansipan mountain\",\n"
            "  \"Vietnam tropical forest\", \"Vietnam floating market\"\n\n"
            "FORBIDDEN for Vietnamese geography documents:\n"
            "- Landscapes from other countries (especially similar Asian countries)\n"
            "- Generic mountain/river/sea photos without Vietnamese context\n"
            "- Random people, buildings, or objects unrelated to geography\n"
            "- Maps of other countries\n\n"
        )
    elif doc_context.get("is_geography"):
        image_keyword_instruction += (
            "GEOGRAPHY DOCUMENT DETECTED — Image keywords must show geographic features.\n"
            "Always include the specific country/region name with the geographic feature.\n"
            "Use: 'Vietnam river delta', not just 'river'. Include specific place names.\n\n"
        )

    image_keyword_instruction += (
        "Response must be valid JSON. slide_number, title, content, and image_keyword are mandatory."
    )

    system_parts.append(image_keyword_instruction)

    system_instruction = "\n\n".join(system_parts)

    # Build user message: article content + user request
    if word_content:
        user_message = f"Here is the article to create slides from:\n\n{word_content}\n\nUser request: {prompt}"
    else:
        user_message = f"User request: {prompt}"

    try:
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=user_message,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.7,
                top_p=1.0,
            ),
        )
        response_text = response.text
        logger.debug(f"LLM raw response: {response_text[:500]}...")

        # Extract JSON from response
        json_str = _extract_json_from_text(response_text)
        if json_str is None:
            logger.error("Could not extract JSON from LLM response")
            raise ValueError("LLM response did not contain valid JSON slide data")

        parsed = json.loads(json_str)

        # Ensure it's a list
        if isinstance(parsed, dict):
            parsed = [parsed]

        # Process content fields (handle non-string content)
        for slide in parsed:
            if "content" in slide:
                slide["content"] = _process_content(slide["content"])
            if "narration" not in slide:
                slide["narration"] = ""

        return {
            "slides": parsed,
            "theme": detect_theme_from_prompt(prompt),
            "document_topic": document_topic,
        }

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        raise ValueError(f"Failed to parse LLM response as JSON: {e}")
    except Exception as e:
        logger.error(f"Error generating slides: {e}")
        raise


def _process_content(input_data) -> str:
    """Process content field which may be string, dict, or list."""
    if isinstance(input_data, str):
        return input_data
    elif isinstance(input_data, dict):
        return '\n'.join(f"{key}: {value}" for key, value in input_data.items())
    elif isinstance(input_data, list):
        output = []
        for item in input_data:
            if isinstance(item, str):
                output.append(item)
            elif isinstance(item, dict):
                output.extend(f"{key}: {value}" for key, value in item.items())
        return '\n'.join(output)
    return str(input_data)


async def generate_slides_chunked(
    prompt: str,
    word_content: str,
    session_id: str = "",
) -> dict:
    """
    Generate slides from a large document by splitting it into chunks
    and generating slides for each chunk separately, then merging.

    This avoids the LLM output token limit that prevents generating
    enough slides from very large documents in a single call.

    Args:
        prompt: User's instruction for slide creation.
        word_content: Large document content.
        session_id: Session ID for progress tracking.

    Returns:
        Dict with 'slides' (list of slide dicts) and 'theme' (theme name string).
    """
    import asyncio
    from .document_service import _split_text_into_chunks

    # Extract document topic from the FULL document (before chunking)
    document_topic = _extract_document_topic(word_content)
    if document_topic:
        logger.info(f"Chunked generation - document topic: '{document_topic[:80]}...'")

    # Dynamic chunk size based on document length
    total_words = len(word_content.split())
    if total_words <= 40000:
        chunk_size = 10000
    elif total_words <= 70000:
        chunk_size = 17000
    else:
        chunk_size = 25000

    chunks = _split_text_into_chunks(word_content, chunk_size)
    total_chunks = len(chunks)

    logger.info(
        f"Chunked slide generation: {len(word_content.split())} words → "
        f"{total_chunks} chunks of ~{chunk_size} words"
    )

    # Initialize progress tracking
    if session_id:
        _generation_progress[session_id] = {
            "current_chunk": 0,
            "total_chunks": total_chunks,
            "percent": 0,
            "status": "starting",
            "message": f"Đang chuẩn bị tạo slides từ {total_chunks} phần...",
        }

    all_slides = []
    slide_offset = 0

    for i, chunk in enumerate(chunks, 1):
        chunk_words = len(chunk.split())
        logger.info(f"Generating slides for chunk {i}/{total_chunks} ({chunk_words} words)")

        # Update progress
        if session_id:
            pct = int(((i - 1) / total_chunks) * 100)
            _generation_progress[session_id] = {
                "current_chunk": i,
                "total_chunks": total_chunks,
                "percent": pct,
                "status": "generating",
                "message": f"Đang tạo slides phần {i}/{total_chunks}...",
            }

        # Build context about already-generated slides to prevent duplication
        existing_titles_note = ""
        if all_slides:
            existing_titles = [s.get("title", "") for s in all_slides]
            existing_titles_note = (
                f"\n\nSlides already created from previous sections:\n"
                + "\n".join(f"- {t}" for t in existing_titles)
                + "\n\nDo NOT create slides about the same topics listed above. "
                "Focus only on NEW information in this section."
            )

        # Each chunk gets its own context about position in the document
        chunk_prompt = (
            f"{prompt}\n\n"
            f"NOTE: This is part {i} of {total_chunks} of the document. "
            f"Create 12-15 slides covering THIS section thoroughly.\n"
            f"CRITICAL: Each slide MUST have rich content with 5-8 bullet points. "
            f"Each bullet point should be a complete, informative sentence. "
            f"Do NOT create slides with only 1-2 lines. "
            f"Extract key facts, dates, names, events, and details from the text."
            + existing_titles_note
        )

        try:
            result = await generate_slides(
                prompt=chunk_prompt,
                word_content=chunk,
                existing_slides=[],
                skip_slide_count=True,
            )
            chunk_slides = result["slides"]

            # Renumber slides with offset
            for slide in chunk_slides:
                slide_offset += 1
                slide["slide_number"] = float(slide_offset)

            all_slides.extend(chunk_slides)
            logger.info(f"  → Chunk {i} generated {len(chunk_slides)} slides")

        except Exception as e:
            error_str = str(e)
            # Determine wait time based on error type
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                wait_time = 62
            elif "503" in error_str or "UNAVAILABLE" in error_str:
                wait_time = 15
            else:
                wait_time = 10  # JSON errors, etc

            logger.warning(f"Chunk {i} failed: {e}. Retrying in {wait_time}s...")
            await asyncio.sleep(wait_time)

            try:
                result = await generate_slides(
                    prompt=chunk_prompt,
                    word_content=chunk,
                    existing_slides=[],
                    skip_slide_count=True,
                )
                chunk_slides = result["slides"]
                for slide in chunk_slides:
                    slide_offset += 1
                    slide["slide_number"] = float(slide_offset)
                all_slides.extend(chunk_slides)
                logger.info(f"  → Chunk {i} generated {len(chunk_slides)} slides (after retry)")
            except Exception as retry_err:
                logger.error(f"Chunk {i} failed after retry, skipping: {retry_err}")
                continue

    if not all_slides:
        if session_id:
            clear_generation_progress(session_id)
        raise ValueError("No slides were generated from any chunk")

    # Final renumber from 1
    for i, slide in enumerate(all_slides, 1):
        slide["slide_number"] = float(i)

    # Mark complete
    if session_id:
        _generation_progress[session_id] = {
            "current_chunk": total_chunks,
            "total_chunks": total_chunks,
            "percent": 100,
            "status": "complete",
            "message": f"Hoàn tất tạo {len(all_slides)} slides",
        }

    logger.info(f"Chunked generation complete: {len(all_slides)} total slides from {total_chunks} chunks")
    return {
        "slides": all_slides,
        "theme": detect_theme_from_prompt(prompt),
        "document_topic": document_topic,
    }

