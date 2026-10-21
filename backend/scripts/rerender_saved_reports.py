from __future__ import annotations
import hashlib, json, urllib.request
from pathlib import Path

base = "http://localhost:10000"
root = Path("artifacts/composition-qa")
out = root / "rerendered"
out.mkdir(exist_ok=True)
for index in (1, 2):
    payload = json.loads((root / f"report-{index}.json").read_text(encoding="utf-8"))
    req = urllib.request.Request(base + "/api/chat/report/pdf", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    data = urllib.request.urlopen(req, timeout=180).read()
    path = out / f"report-{index}.pdf"
    path.write_bytes(data)
    import pymupdf
    document = pymupdf.open(stream=data, filetype="pdf")
    for page_number, page in enumerate(document, start=1):
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25), alpha=False)
        pix.save(str(out / f"report-{index}-{page_number:02d}.png"))
    print(index, len(data), hashlib.sha256(data).hexdigest())
