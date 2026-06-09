from __future__ import annotations

import hashlib
import re
from typing import Any

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def parse_markdown_document(path: str, text: str) -> dict[str, Any]:
    """Parse Markdown/frontmatter deterministically without LLMs or network calls."""
    metadata: dict[str, Any] = {}
    body = text
    match = _FRONTMATTER_RE.match(text)
    if match:
        loaded = yaml.safe_load(match.group(1)) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"frontmatter in {path} must be a mapping")
        metadata = loaded
        body = text[match.end() :]

    headings = [m.group(2).strip() for m in _HEADING_RE.finditer(body)]
    stable_id = hashlib.sha256(f"{path}\n{text}".encode("utf-8")).hexdigest()[:16]
    title = str(metadata.get("title") or (headings[0] if headings else path))
    return {
        "id": stable_id,
        "path": path,
        "title": title,
        "metadata": metadata,
        "headings": headings,
        "body": body.strip(),
    }
