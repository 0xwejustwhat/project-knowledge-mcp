from __future__ import annotations

import hashlib
from typing import Any


def parse_text_document(path: str, text: str) -> dict[str, Any]:
    """Parse plain text deterministically without LLMs, embeddings, or network calls."""
    body = text.strip()
    first_line = next((line.strip() for line in body.splitlines() if line.strip()), path)
    stable_id = hashlib.sha256(f"{path}\n{text}".encode("utf-8")).hexdigest()[:16]
    return {
        "id": stable_id,
        "path": path,
        "title": first_line[:120] or path,
        "metadata": {"type": "text", "status": "captured"},
        "headings": [],
        "body": body,
    }
