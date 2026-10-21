from pathlib import Path
import sys
import types

from routes import pdf


def test_chromium_pdf_render_disables_browser_file_path_furniture(monkeypatch):
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        output = next(item.split("=", 1)[1] for item in command if item.startswith("--print-to-pdf="))
        Path(output).write_bytes(b"%PDF-test")
        return type("Result", (), {"returncode": 0, "stderr": b""})()

    # Use the running Python executable as a harmless stand-in. subprocess.run is mocked.
    monkeypatch.setitem(sys.modules, "shutil", types.SimpleNamespace(which=lambda _name: sys.executable))
    monkeypatch.setitem(sys.modules, "subprocess", types.SimpleNamespace(PIPE=-1, run=fake_run))

    assert pdf._pdf_with_chromium("<html><body>report</body></html>") == b"%PDF-test"
    assert "--no-pdf-header-footer" in observed["command"]
    assert "--print-to-pdf-no-header" not in observed["command"]
