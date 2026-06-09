"""
PDF Service — Convert a generated PPTX file to PDF.

This module provides a small helper that invokes LibreOffice in headless mode
to perform the conversion. LibreOffice is commonly available in the Docker
image used for the API (see `docker/api/Dockerfile`). If LibreOffice is not
installed the function will raise a clear ``RuntimeError``.

The conversion is performed synchronously because it is fast for the typical
size of a presentation (a few megabytes). The function returns the path to the
generated PDF file placed in the same temporary directory as the source PPTX.
"""

import subprocess
from pathlib import Path
from typing import Union

from ..core.config import settings


def _ensure_libreoffice() -> None:
    """Check that ``soffice`` (LibreOffice) is available.

    ``subprocess.run`` with ``--version`` is cheap and will raise ``FileNotFoundError``
    if the binary is missing. We surface a ``RuntimeError`` with a helpful
    message so callers can handle the situation gracefully.
    """
    try:
        subprocess.run(["soffice", "--version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "LibreOffice (soffice) is required for PDF export but was not found. "
            "Install it in the Docker image or the host system."
        ) from exc


def convert_pptx_to_pdf(pptx_path: Union[Path, str]) -> Path:
    """Convert *pptx_path* to a PDF file using LibreOffice.

    Parameters
    ----------
    pptx_path: Path | str
        Path to the source ``.pptx`` file.

    Returns
    -------
    Path
        Path to the generated ``.pdf`` file placed alongside the source file.
    """
    _ensure_libreoffice()

    pptx_path = Path(pptx_path)
    if not pptx_path.exists():
        raise FileNotFoundError(f"PPTX file not found: {pptx_path}")

    # LibreOffice converts files in‑place inside the output directory we give it.
    # We use the temporary directory defined in settings to avoid permission
    # issues and to keep the output tidy.
    output_dir = settings.TEMP_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    # ``--headless`` runs without GUI, ``--convert-to pdf`` performs the conversion.
    # ``--outdir`` specifies where the resulting PDF will be written.
    subprocess.run(
        [
            "soffice",
            "--headless",
            "--convert-to",
            "pdf",
            str(pptx_path),
            "--outdir",
            str(output_dir),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    pdf_path = output_dir / pptx_path.with_suffix('.pdf').name
    if not pdf_path.exists():
        raise RuntimeError(f"PDF conversion failed, expected file not found: {pdf_path}")
    return pdf_path
