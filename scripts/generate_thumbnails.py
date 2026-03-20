"""
Generate PNG thumbnails from all PPTX templates using PowerPoint COM automation.
Uses comtypes to control PowerPoint on Windows.
Run: D:\\AI_project\\torch_gpu\\Scripts\\python.exe scripts/generate_thumbnails.py
"""
import os
import sys
import time
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "templates"
THUMBNAILS_DIR = ROOT / "assets" / "thumbnails"

# Thumbnail settings
THUMB_WIDTH = 960
THUMB_HEIGHT = 540  # 16:9 aspect ratio

# Theme categories
CATEGORIES = ["business", "creative", "education", "technology"]


def generate_all_thumbnails():
    """Generate thumbnails for all templates using PowerPoint COM."""
    import comtypes.client

    THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

    # Start PowerPoint
    print("Starting PowerPoint...")
    ppt_app = comtypes.client.CreateObject("PowerPoint.Application")
    ppt_app.Visible = 1  # Must be visible for export to work properly

    try:
        total = 0
        for cat in CATEGORIES:
            cat_dir = TEMPLATES_DIR / cat
            if not cat_dir.exists():
                continue

            print(f"\n{'='*50}")
            print(f"  Category: {cat}")
            print(f"{'='*50}")

            for pptx_file in sorted(cat_dir.glob("*.pptx")):
                theme_id = pptx_file.stem
                print(f"\n  Processing: {theme_id}")

                try:
                    # Open presentation
                    pptx_path = str(pptx_file.resolve())
                    presentation = ppt_app.Presentations.Open(
                        pptx_path,
                        ReadOnly=True,
                        Untitled=False,
                        WithWindow=False,
                    )

                    # Export each slide as PNG
                    slide_count = presentation.Slides.Count
                    for slide_idx in range(1, slide_count + 1):
                        slide = presentation.Slides(slide_idx)
                        output_path = THUMBNAILS_DIR / f"{theme_id}_slide_{slide_idx}.png"

                        # Export slide as image
                        slide.Export(
                            str(output_path.resolve()),
                            "PNG",
                            THUMB_WIDTH,
                            THUMB_HEIGHT,
                        )

                        size_kb = output_path.stat().st_size / 1024
                        print(f"    Slide {slide_idx}: {output_path.name} ({size_kb:.0f} KB)")

                    presentation.Close()
                    total += 1
                    print(f"    ✅ Done ({slide_count} slides)")

                except Exception as e:
                    print(f"    ❌ Error: {e}")

        print(f"\n{'='*50}")
        print(f"  ✨ Generated thumbnails for {total} templates!")
        print(f"  📂 Output: {THUMBNAILS_DIR}")
        print(f"{'='*50}")

    finally:
        try:
            ppt_app.Quit()
        except Exception:
            pass


if __name__ == "__main__":
    generate_all_thumbnails()
