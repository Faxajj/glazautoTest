"""Fail if git conflict markers are found in tracked text files."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

MARKERS = ("<<<<<<< ", "=======", ">>>>>>> ")
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".db", ".zip", ".pyc"}


def tracked_files() -> list[Path]:
    out = subprocess.check_output(["git", "ls-files"], text=True)
    return [Path(line.strip()) for line in out.splitlines() if line.strip()]


def is_binary(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except Exception:
        return True
    return b"\x00" in data


def main() -> int:
    bad: list[str] = []
    for path in tracked_files():
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if not path.exists() or is_binary(path):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, start=1):
            if any(line.startswith(m) for m in MARKERS):
                bad.append(f"{path}:{i}: {line[:80]}")

    if bad:
        print("Found unresolved conflict markers:")
        for row in bad:
            print(row)
        return 1

    print("OK: no conflict markers found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
