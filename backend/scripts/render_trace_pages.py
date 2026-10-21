from pathlib import Path
from PIL import Image, ImageDraw
import pymupdf

root = Path("artifacts/pdf-trace/browser")
for name in "ABC":
    document = pymupdf.open(root / f"download-{name}.pdf")
    output = root / f"pages-{name}"
    output.mkdir(exist_ok=True)
    thumbs = []
    for index, page in enumerate(document, 1):
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25), alpha=False)
        path = output / f"page-{index:02d}.png"
        pixmap.save(str(path))
        image = Image.open(path).convert("RGB")
        width = 250
        height = round(image.height * width / image.width)
        thumb = image.resize((width, height))
        canvas = Image.new("RGB", (width, height + 22), "white")
        canvas.paste(thumb, (0, 22))
        ImageDraw.Draw(canvas).text((5, 4), f"Page {index}", fill="black")
        thumbs.append(canvas)
    columns = 4
    cell_height = max(image.height for image in thumbs)
    sheet = Image.new("RGB", (columns * 250, ((len(thumbs) + columns - 1) // columns) * cell_height), (220, 220, 220))
    for index, image in enumerate(thumbs):
        sheet.paste(image, ((index % columns) * 250, (index // columns) * cell_height))
    sheet.save(root / f"contact-{name}.png")
