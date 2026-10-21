"""Build compact before/after review sheets for the three scoped reports."""
from pathlib import Path
from PIL import Image, ImageOps, ImageDraw

ROOT = Path("artifacts")
CASES = {
    "01-quick-commerce": {"before": [1, 3, 4], "after": [1, 3, 4]},
    "02-banking-cycle": {"before": [1, 2, 3], "after": [1, 2, 3]},
    "09-renewable-energy": {"before": [1, 2, 3], "after": [1, 2, 3]},
}

def sheet(slug: str, side: str, pages: list[int]) -> None:
    src = ROOT / ("client-demo-10-reports" if side == "before" else "representative-refinement") / slug
    imgs = []
    for n in pages:
        path = src / f"page-{n:02d}.png"
        if not path.exists():
            continue
        image = Image.open(path).convert("RGB")
        image.thumbnail((360, 520))
        canvas = Image.new("RGB", (380, 565), "white")
        x, y = (canvas.width - image.width) // 2, 28
        canvas.paste(image, (x, y))
        ImageDraw.Draw(canvas).text((16, 8), f"{side.upper()} · page {n}", fill="#202522")
        imgs.append(canvas)
    out = Image.new("RGB", (380 * len(imgs), 565), "#eceeea")
    for i, image in enumerate(imgs):
        out.paste(image, (i * 380, 0))
    dest = ROOT / "representative-refinement" / slug
    dest.mkdir(parents=True, exist_ok=True)
    out.save(dest / f"{side}-review-sheet.png")

for slug, spec in CASES.items():
    sheet(slug, "before", spec["before"])
    sheet(slug, "after", spec["after"])
