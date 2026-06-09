from project_knowledge_mcp.server import create_mcp
from spikes.shared.markdown_parse import parse_markdown_document


def test_create_mcp_server():
    assert create_mcp().name == "Project Knowledge MCP"


def test_markdown_frontmatter_and_headings():
    doc = parse_markdown_document(
        path="docs/example.md",
        text="""---
title: Example
authority: canonical
tags: [mvp, step0]
---
# Heading
Body""",
    )
    assert doc["metadata"]["authority"] == "canonical"
    assert doc["metadata"]["tags"] == ["mvp", "step0"]
    assert doc["headings"] == ["Heading"]
