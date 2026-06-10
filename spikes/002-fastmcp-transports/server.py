from __future__ import annotations

import sys

from project_knowledge_mcp.server import cli

if __name__ == "__main__":
    if len(sys.argv) == 1 or sys.argv[1].startswith("--"):
        sys.argv.insert(1, "serve")
    cli()
