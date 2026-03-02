from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
EXCLUDE_FILES = {".env.example"}

PATTERNS = {
    "openai_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "github_pat": re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    "slack_token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "telegram_bot_token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
}


def iter_files(root: Path):
    for p in root.rglob("*"):
        if p.is_dir():
            if p.name in EXCLUDE_DIRS:
                continue
            continue
        if p.name in EXCLUDE_FILES:
            continue
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        yield p


def scan_file(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    findings = []
    for name, rx in PATTERNS.items():
        if rx.search(text):
            findings.append(name)
    return findings


def main() -> int:
    findings = []
    for f in iter_files(ROOT):
        kinds = scan_file(f)
        for k in kinds:
            findings.append((f, k))

    if findings:
        print("Potential secrets detected:")
        for f, k in findings:
            print(f"- {k}: {f.relative_to(ROOT)}")
        return 1

    print("Secret scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
