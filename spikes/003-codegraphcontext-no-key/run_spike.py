from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURE_REPO = ROOT / "fixture_repo"
TMP = ROOT / ".tmp"
DB = TMP / "kuzu"


def run_cgc(*args: str, timeout: int = 120) -> dict:
    env = os.environ.copy()
    for key in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY"]:
        env.pop(key, None)
    cmd = [
        sys.executable,
        "-m",
        "codegraphcontext.cli.main",
        "--db",
        "kuzudb",
        "--path",
        str(DB),
        *args,
    ]
    proc = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout)
    return {
        "cmd": cmd[3:],
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def main() -> None:
    if DB.exists():
        if DB.is_dir():
            shutil.rmtree(DB)
        else:
            DB.unlink()
    TMP.mkdir(exist_ok=True)
    steps = {
        "doctor": run_cgc("doctor", timeout=180),
        "index": run_cgc("index", str(FIXTURE_REPO), "--force", timeout=180),
        "stats": run_cgc("stats", timeout=180),
        "find_calculator": run_cgc("find", "name", "Calculator", timeout=180),
        "find_add": run_cgc("find", "name", "add", timeout=180),
    }
    calculator_output = steps["find_calculator"]["stdout"] + steps["find_calculator"]["stderr"]
    passed = (
        all(step["exit_code"] == 0 for step in steps.values()) and "Calculator" in calculator_output
    )
    report = {
        "no_key_environment_for_child_commands": True,
        "fixture_repo": str(FIXTURE_REPO),
        "steps": steps,
        "verdict": "PARTIAL" if passed else "INVALIDATED",
        "reason": "Core no-key install/index/search path works; machine-readable response shape still needs adapter validation.",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
