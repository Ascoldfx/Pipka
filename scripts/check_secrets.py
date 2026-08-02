#!/usr/bin/env python3
"""Fail CI when a tracked file contains a credential-shaped value.

This is intentionally dependency-free and scans the Git index rather than the
whole working directory, so local ``.env`` files are never opened or printed.
It is a last-line guard; provider-side secret rotation remains mandatory after
any historical leak.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
MAX_TEXT_BYTES = 2 * 1024 * 1024

# Keep names generic in output: CI should identify the file and line, never
# echo the suspected secret itself.
PATTERNS = {
    "Telegram bot token": re.compile(rb"\b\d{8,12}:[A-Za-z0-9_-]{25,}\b"),
    "Anthropic API key": re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    "Google API key": re.compile(rb"\bAIza[A-Za-z0-9_-]{30,}\b"),
    "GitHub token": re.compile(rb"\b(?:gh[opusr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw]


def main() -> int:
    findings: list[tuple[str, int, str]] = []
    for path in tracked_files():
        if path.resolve() == SELF or not path.is_file() or path.stat().st_size > MAX_TEXT_BYTES:
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        lines = data.splitlines()
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(data):
                line = data.count(b"\n", 0, match.start()) + 1
                if b"secret-scan: allow" in lines[line - 1]:
                    continue
                findings.append((str(path.relative_to(ROOT)), line, label))

    if findings:
        print("Credential-shaped values found in tracked files:")
        for path, line, label in findings:
            print(f"- {path}:{line}: {label}")
        print("Remove the value, rotate it at the provider, and purge Git history if it was pushed.")
        return 1

    print("No credential-shaped values found in tracked files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
