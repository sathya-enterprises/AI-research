"""Re-render the final client JSON contracts with the current Chromium renderer."""
from pathlib import Path
import hashlib, json, urllib.request
import pymupdf

base = "http://localhost:10000"
root = Path("artifacts/client-demo-10-reports")
for folder in sorted(root.iterdir()):
    if not folder.is_dir() or not (folder / "report.json").exists():
        continue
    payload = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    report_text = str(payload.get("report") or "")
    presentation = payload.get("presentation")
    if isinstance(presentation, dict) and len(report_text) < 14000:
        cover = presentation.get("cover")
        if isinstance(cover, dict) and cover.get("enabled", True) and cover.get("treatment") in {"classic", "bold_banner", "data_driven"}:
            cover["enabled"] = False
            payload["presentation"] = presentation
            (folder / "report.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    req = urllib.request.Request(base + "/api/chat/report/pdf", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    pdf = urllib.request.urlopen(req, timeout=300).read()
    (folder / "report.pdf").write_bytes(pdf)
    for stale in folder.glob("page-*.png"):
        stale.unlink()
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    for i, page in enumerate(doc, 1):
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25), alpha=False)
        pix.save(str(folder / f"page-{i:02d}.png"))
    print(folder.name, len(doc), hashlib.sha256(pdf).hexdigest())
