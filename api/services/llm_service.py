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
        Dict with 'slides' (list of slide dicts) and 'theme' (theme name string).
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

    system_parts.append(
        "LANGUAGE RULE: The title and content of each slide MUST be written in the SAME language "
        "as the input article. If the article is in Vietnamese, write slides in Vietnamese. "
        "If the article is in English, write slides in English. "
        "ONLY the image_keyword field should always be in English.\n\n"
        "The narration field should be left empty unless explicitly requested. "
        "The image_keyword field MUST contain 1-2 simple English words for a relevant photo. "
        "Response must be valid JSON. slide_number, title, content, and image_keyword are mandatory."
    )

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

        return {"slides": parsed, "theme": detect_theme_from_prompt(prompt)}

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
) -> dict:
    """
    Generate slides from a large document by splitting it into chunks
    and generating slides for each chunk separately, then merging.

    This avoids the LLM output token limit that prevents generating
    enough slides from very large documents in a single call.

    Args:
        prompt: User's instruction for slide creation.
        word_content: Large document content.

    Returns:
        Dict with 'slides' (list of slide dicts) and 'theme' (theme name string).
    """
    import asyncio
    from .document_service import _split_text_into_chunks

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

    all_slides = []
    slide_offset = 0

    for i, chunk in enumerate(chunks, 1):
        chunk_words = len(chunk.split())
        logger.info(f"Generating slides for chunk {i}/{total_chunks} ({chunk_words} words)")

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
        raise ValueError("No slides were generated from any chunk")

    # Final renumber from 1
    for i, slide in enumerate(all_slides, 1):
        slide["slide_number"] = float(i)

    logger.info(f"Chunked generation complete: {len(all_slides)} total slides from {total_chunks} chunks")
    return {"slides": all_slides, "theme": detect_theme_from_prompt(prompt)}

