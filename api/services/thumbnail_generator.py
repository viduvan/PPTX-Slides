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
THUMBNAIL_WIDTH = 960  # px width for thumbnail images


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
