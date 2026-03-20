"""Verify thumbnails: list all PNG files with sizes."""
from pathlib import Path
from collections import defaultdict

THUMBNAILS_DIR = Path(r'd:\AI_project\PPTX-Slides\assets\thumbnails')
TEMPLATES_DIR = Path(r'd:\AI_project\PPTX-Slides\templates')

expected_themes = []
for cat_dir in sorted(TEMPLATES_DIR.iterdir()):
    if not cat_dir.is_dir():
        continue
    for pptx_file in sorted(cat_dir.glob('*.pptx')):
        expected_themes.append(pptx_file.stem)

all_pngs = sorted(THUMBNAILS_DIR.glob('*.png'))

by_theme = defaultdict(list)
for png in all_pngs:
    name = png.stem
    parts = name.rsplit('_slide_', 1)
    if len(parts) == 2:
        by_theme[parts[0]].append((parts[1], png))

print(f"Expected: {len(expected_themes)} themes, Total PNGs: {len(all_pngs)}")
print()
for theme in expected_themes:
    slides = by_theme.get(theme, [])
    if slides:
        sizes = [f"s{s}:{p.stat().st_size/1024:.0f}KB" for s, p in sorted(slides)]
        print(f"  OK {theme:25s} {len(slides)} slides  [{', '.join(sizes)}]")
    else:
        print(f"  MISSING {theme}")

extra = set(by_theme.keys()) - set(expected_themes)
if extra:
    print(f"\nExtra themes: {extra}")
