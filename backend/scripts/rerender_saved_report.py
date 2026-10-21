"""Rerender one saved production report JSON through the normal PDF endpoint."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from urllib.request import Request, urlopen
import pymupdf

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--base", default="http://localhost:10000")
    a = p.parse_args()
    payload = json.loads(Path(a.input).read_text(encoding="utf-8"))
    if not payload.get("question"):
        qa_path = Path(a.input).with_name("qa.json")
        if qa_path.exists():
            try:
                payload["question"] = json.loads(qa_path.read_text(encoding="utf-8")).get("query", "")
            except Exception:
                pass
    req = Request(a.base.rstrip("/") + "/api/chat/report/pdf", data=json.dumps(payload).encode(), headers={"Content-Type":"application/json"}, method="POST")
    pdf = urlopen(req, timeout=300).read()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "report.pdf").write_bytes(pdf)
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    for n, page in enumerate(doc, 1):
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25), alpha=False)
        pix.save(str(out / f"page-{n:02d}.png"))
    print(json.dumps({"pages": len(doc), "bytes": len(pdf), "out": str(out)}))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
