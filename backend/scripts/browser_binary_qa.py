from __future__ import annotations
import hashlib, json
from pathlib import Path
from playwright.sync_api import sync_playwright

payload = json.loads(Path("artifacts/composition-qa/report-1.json").read_text(encoding="utf-8"))
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(accept_downloads=True)
    page.goto("http://localhost:3000", wait_until="domcontentloaded", timeout=30_000)
    result = page.evaluate("""async (body) => {
      const response = await fetch('/api/chat/report/pdf', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(body)});
      const bytes = new Uint8Array(await response.arrayBuffer());
      const hash = async (value) => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', value))).map(x => x.toString(16).padStart(2,'0')).join('');
      const proxySha = await hash(bytes);
      const objectUrl = URL.createObjectURL(new Blob([bytes], {type:'application/pdf'}));
      const viewerBytes = new Uint8Array(await (await fetch(objectUrl)).arrayBuffer());
      const viewerSha = await hash(viewerBytes);
      const anchor = document.createElement('a'); anchor.href = objectUrl; anchor.download = 'browser-verified.pdf'; document.body.appendChild(anchor);
      return {status: response.status, byteLength: bytes.length, backendSha: response.headers.get('x-pdf-sha256'), proxySha, viewerSha, viewerMatchesProxy: viewerSha === proxySha, objectUrlPrefix: objectUrl.slice(0,5)};
    }""", payload)
    with page.expect_download(timeout=10_000) as download_info:
        page.evaluate("document.querySelector('a[download]').click()")
    download = download_info.value
    result["downloadSuggestedFilename"] = download.suggested_filename
    print(json.dumps(result, indent=2))
    browser.close()
