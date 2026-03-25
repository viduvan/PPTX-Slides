"""
Thumbnail Generator — Converts PPTX template slides to PNG thumbnail images.
Uses LibreOffice (headless) for PPTX→PDF and pdftoppm for PDF→PNG.
Caches generated thumbnails in assets/thumbnails/.
"""
import logging
import subprocess
import tempfile
from pathlib import Path

from ..core.config import settings
from .template_builder import THEME_REGISTRY, AVAILABLE_THEMES

logger = logging.getLogger("pptx_api.thumbnail_generator")

THUMBNAILS_DIR = Path(settings.BASE_DIR) / "assets" / "thumbnails"
THUMBNAIL_WIDTH = 960  # px width for theme thumbnail images (high quality, cached once)
SESSION_THUMB_WIDTH = 1280  # px width for session thumbnails
SESSION_THUMB_DPI = 150  # DPI for session thumbnail generation


def _ensure_dir():
    """Ensure the thumbnails directory exists."""
    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)


def get_thumbnail_paths(theme_id: str) -> list[Path]:
    """Return list of existing thumbnail PNGs for a theme, sorted by slide number."""
    _ensure_dir()
    pattern = f"{theme_id}_slide_*.png"
    paths = sorted(THUMBNAILS_DIR.glob(pattern))
    return paths


def generate_thumbnails(theme_id: str, force: bool = False) -> list[Path]:
    """
    Generate PNG thumbnails for all slides of a theme's PPTX template.
    Returns list of PNG file paths, one per slide.
    """
    _ensure_dir()

    # Check if already cached
    existing = get_thumbnail_paths(theme_id)
    if existing and not force:
        logger.debug(f"Thumbnails already cached for '{theme_id}': {len(existing)} slides")
        return existing

    # Find the PPTX template
    reg = THEME_REGISTRY.get(theme_id)
    if not reg:
        logger.warning(f"Theme '{theme_id}' not in registry")
        return []

    category = reg["category"]
    pptx_path = settings.TEMPLATES_DIR / category / f"{theme_id}.pptx"
    if not pptx_path.exists():
        logger.warning(f"Template file not found: {pptx_path}")
        return []

    logger.info(f"Generating thumbnails for '{theme_id}' from {pptx_path}")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Step 1: PPTX → PDF via LibreOffice
            result = subprocess.run(
                [
                    "libreoffice", "--headless", "--convert-to", "pdf",
                    "--outdir", str(tmpdir), str(pptx_path),
                ],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                logger.error(f"LibreOffice conversion failed: {result.stderr}")
                return []

            pdf_path = tmpdir / f"{theme_id}.pdf"
            if not pdf_path.exists():
                logger.error(f"PDF not created at expected path: {pdf_path}")
                return []

            # Step 2: PDF → PNG via pdftoppm
            output_prefix = str(tmpdir / "slide")
            result = subprocess.run(
                [
                    "pdftoppm", "-png", "-r", "150",
                    "-scale-to-x", str(THUMBNAIL_WIDTH),
                    "-scale-to-y", "-1",  # maintain aspect ratio
                    str(pdf_path), output_prefix,
                ],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                logger.error(f"pdftoppm failed: {result.stderr}")
                return []

            # Step 3: Move PNGs to thumbnails dir with proper naming
            raw_pngs = sorted(tmpdir.glob("slide-*.png"))
            output_paths = []
            for i, png in enumerate(raw_pngs, start=1):
                dest = THUMBNAILS_DIR / f"{theme_id}_slide_{i}.png"
                png.rename(dest)
                output_paths.append(dest)
                logger.debug(f"  Slide {i}: {dest.name} ({dest.stat().st_size / 1024:.0f} KB)")

            logger.info(f"Generated {len(output_paths)} thumbnails for '{theme_id}'")
            return output_paths

    except subprocess.TimeoutExpired:
        logger.error(f"Thumbnail generation timed out for '{theme_id}'")
        return []
    except Exception as e:
        logger.error(f"Thumbnail generation error for '{theme_id}': {e}")
        return []


def generate_all_thumbnails(force: bool = False) -> int:
    """Generate thumbnails for all available themes. Returns count of themes processed."""
    _ensure_dir()
    count = 0
    for theme_id in AVAILABLE_THEMES:
        paths = generate_thumbnails(theme_id, force=force)
        if paths:
            count += 1
    logger.info(f"Generated thumbnails for {count}/{len(AVAILABLE_THEMES)} themes")
    return count


# ── Session Slide Thumbnails ────────────────────────────────


def get_session_thumbnail_paths(session_id: str) -> list[Path]:
    """Return list of existing thumbnail JPEGs for a session, sorted by slide number."""
    _ensure_dir()
    pattern = f"session_{session_id}_slide_*.jpg"
    paths = sorted(THUMBNAILS_DIR.glob(pattern))
    return paths


def generate_session_thumbnails(session_id: str, pptx_path: str | Path) -> list[Path]:
    """
    Generate JPEG thumbnails progressively, one page at a time.
    Each thumbnail is written to the final dir immediately so the frontend
    can display slides as they become available via polling.

    Args:
        session_id: Unique session identifier.
        pptx_path: Path to the generated PPTX file.

    Returns:
        List of JPEG file paths, one per slide.
    """
    _ensure_dir()
    pptx_path = Path(pptx_path)

    # Check if already fully cached
    existing = get_session_thumbnail_paths(session_id)
    if existing:
        logger.debug(f"Session thumbnails already cached for '{session_id}': {len(existing)} slides")
        return existing

    if not pptx_path.exists():
        logger.warning(f"PPTX file not found: {pptx_path}")
        return []

    logger.info(f"Generating session thumbnails for '{session_id}' from {pptx_path}")

    try:
        # Use a persistent temp dir for the PDF (cleaned up at the end)
        pdf_dir = Path(settings.TEMP_DIR) / f"thumb_{session_id}"
        pdf_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = pdf_dir / f"{pptx_path.stem}.pdf"

        # Step 1: PPTX → PDF via LibreOffice (one-time, ~10-20s)
        if not pdf_path.exists():
            result = subprocess.run(
                [
                    "libreoffice", "--headless", "--convert-to", "pdf",
                    "--outdir", str(pdf_dir), str(pptx_path),
                ],
                capture_output=True, text=True, timeout=180,
            )
            if result.returncode != 0:
                logger.error(f"LibreOffice conversion failed: {result.stderr}")
                return []

            if not pdf_path.exists():
                logger.error(f"PDF not created at expected path: {pdf_path}")
                return []

        # Step 2: Get total page count via pdfinfo
        total_pages = _get_pdf_page_count(pdf_path)
        if total_pages <= 0:
            logger.error(f"Could not determine page count for {pdf_path}")
            return []

        logger.info(f"PDF has {total_pages} pages. Rendering thumbnails progressively...")

        # Step 3: Render page by page — each thumbnail appears immediately
        output_paths = []
        for page_num in range(1, total_pages + 1):
            dest = THUMBNAILS_DIR / f"session_{session_id}_slide_{page_num}.jpg"
            if dest.exists():
                output_paths.append(dest)
                continue

            # Render single page to a temp file, then move
            result = subprocess.run(
                [
                    "pdftoppm", "-jpeg", "-jpegopt", "quality=90",
                    "-r", str(SESSION_THUMB_DPI),
                    "-scale-to-x", str(SESSION_THUMB_WIDTH),
                    "-scale-to-y", "-1",
                    "-f", str(page_num), "-l", str(page_num),
                    str(pdf_path), str(pdf_dir / "page"),
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                logger.warning(f"pdftoppm failed for page {page_num}: {result.stderr}")
                continue

            # pdftoppm names the file with zero-padded page number
            rendered = sorted(pdf_dir.glob("page-*.jpg"))
            if rendered:
                rendered[0].rename(dest)
                output_paths.append(dest)

        logger.info(f"Generated {len(output_paths)}/{total_pages} session thumbnails for '{session_id}'")

        # Cleanup temp PDF dir
        import shutil
        shutil.rmtree(pdf_dir, ignore_errors=True)

        return output_paths

    except subprocess.TimeoutExpired:
        logger.error(f"Session thumbnail generation timed out for '{session_id}'")
        return []
    except Exception as e:
        logger.error(f"Session thumbnail generation error for '{session_id}': {e}")
        return []


def _get_pdf_page_count(pdf_path: Path) -> int:
    """Get total number of pages in a PDF using pdfinfo."""
    try:
        result = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if line.startswith("Pages:"):
                return int(line.split(":")[1].strip())
    except Exception as e:
        logger.warning(f"pdfinfo failed: {e}")
    return 0


def cleanup_session_thumbnails(session_id: str) -> int:
    """Remove all cached thumbnails for a session (both jpg and legacy png). Returns count deleted."""
    _ensure_dir()
    count = 0
    for ext in ("*.jpg", "*.png"):
        for p in THUMBNAILS_DIR.glob(f"session_{session_id}_slide_{ext}"):
            p.unlink(missing_ok=True)
            count += 1
    return count
