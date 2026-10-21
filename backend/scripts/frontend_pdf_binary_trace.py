"""Verify the browser fetch/blob/object-URL/download boundary."""
from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path

import pymupdf
from playwright.sync_api import sync_playwright


def stats(path: Path) -> dict:
    data = path.read_bytes()
    doc = pymupdf.open(stream=data, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "pages": len(doc), "text_chars": len(text), "content_type": "application/pdf"}


def main() -> None:
    root = Path(os.environ.get("GG_PDF_TRACE_DIR", "artifacts/pdf-trace"))
    output = root / "browser"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(os.environ.get("FRONTEND_URL", "http://localhost:3000"), wait_until="domcontentloaded")
        for name in ("A", "B", "C"):
            report = json.loads((root / f"report-{name}.json").read_text(encoding="utf-8"))
            result = page.evaluate(
                """
                async ({ report, name }) => {
                  const res = await fetch('/api/chat/report/pdf?artifact=' + encodeURIComponent(name), {
                    method: 'POST', cache: 'no-store',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(report),
                  });
                  const bytes = await res.arrayBuffer();
                  const blob = new Blob([bytes], { type: 'application/pdf' });
                  const url = URL.createObjectURL(blob);
                  window.__qaPdfUrl = url;
                  const frame = document.createElement('iframe');
                  frame.id = 'qa-pdf-viewer';
                  frame.title = 'Final rendered research document';
                  frame.style.cssText = 'position:fixed;inset:0;width:100vw;height:100vh;background:#525659;z-index:2147483647';
                  frame.src = url + '#view=FitH';
                  document.body.appendChild(frame);
                  await new Promise(resolve => { frame.onload = resolve; setTimeout(resolve, 3000); });
                  const anchor = document.createElement('a');
                  anchor.id = 'qa-pdf-download'; anchor.href = url; anchor.download = `growth-gradual-report-${name}.pdf`;
                  anchor.textContent = 'download'; anchor.style.display = 'none'; document.body.appendChild(anchor);
                  return {
                    status: res.status,
                    responseContentType: res.headers.get('content-type'),
                    responseBytes: res.headers.get('x-pdf-bytes'),
                    responsePages: res.headers.get('x-pdf-pages'),
                    responseSha256: res.headers.get('x-pdf-sha256'),
                    arrayBufferBytes: bytes.byteLength,
                    blobBytes: blob.size,
                    blobType: blob.type,
                    objectUrl: url,
                    viewerSrc: frame.src,
                  };
                }
                """,
                {"report": report, "name": name},
            )
            with page.expect_download(timeout=30_000) as download_info:
                page.locator("#qa-pdf-download").evaluate("e => e.click()")
            download = download_info.value
            download_path = output / f"download-{name}.pdf"
            download.save_as(str(download_path))
            page.screenshot(path=str(output / f"viewer-{name}.png"), full_page=False)
            results.append({"report": name, "fetch_blob_viewer": result, "downloaded_pdf": stats(download_path)})
            page.locator("#qa-pdf-viewer").evaluate("e => e.remove()")
            page.locator("#qa-pdf-download").evaluate("e => e.remove()")
        browser.close()
    (output / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
