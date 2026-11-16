"""The frontend chat workspace's source for source-reading tests.

The workspace is frontend/src/components/GrowthGradualChat.tsx plus the modules split out of it in
components/chat/ (report panel, charts, markdown, attachments, styles...). Tests that check frontend
wiring read all of it, so moving code between those files does not break them.
"""
from pathlib import Path


def read_chat_source(root: Path | None = None) -> str:
    root = root or Path(__file__).resolve().parents[2]
    components = root / "frontend" / "src" / "components"
    parts = [(components / "GrowthGradualChat.tsx").read_text(encoding="utf-8")]
    chat_dir = components / "chat"
    if chat_dir.is_dir():
        parts += [p.read_text(encoding="utf-8") for p in sorted(chat_dir.iterdir()) if p.suffix in {".ts", ".tsx", ".css"}]
    return "\n".join(parts)
