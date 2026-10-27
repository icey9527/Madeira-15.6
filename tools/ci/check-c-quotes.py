#!/usr/bin/env python3
"""Catch a string literal left open at the end of a line.

Three build failures in a row came from the same mistake: text that was
supposed to contain the two characters backslash-n reached the source as a real
newline, splitting a literal. The compiler only sees it on a 20-minute CI run,
so check for it before pushing:

    python3 tools/ci/check-c-quotes.py [paths...]   # default: build app

Counts unescaped double quotes per line after stripping // comments, and
ignores lines that end in a backslash (continuations) and lines that are inside
a block comment.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ("build", "app")
SUFFIXES = {".c", ".h", ".m", ".mm", ".cpp", ".hpp"}


def strip_line_comment(line):
    in_string = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            in_string = not in_string
        elif not in_string and char == "/" and line[index:index + 2] == "//":
            return line[:index]
    return line


def check(path):
    problems = []
    in_block = False
    for number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw
        if in_block:
            end = line.find("*/")
            if end < 0:
                continue
            line = line[end + 2:]
            in_block = False
        while True:
            start = line.find("/*")
            if start < 0:
                break
            end = line.find("*/", start + 2)
            if end < 0:
                line = line[:start]
                in_block = True
                break
            line = line[:start] + line[end + 2:]
        line = strip_line_comment(line).rstrip()
        # char literals like '"' are one quote each and pair up on their own
        line = line.replace("'" + chr(34) + "'", "").replace("'" + chr(92) + chr(39) + "'", "")
        if line.endswith("\\"):
            continue
        quotes = line.count('"')
        if quotes % 2:
            problems.append((number, raw.strip()[:100]))
    return problems


def main(argv):
    targets = [Path(arg) for arg in argv] or [ROOT / name for name in DEFAULT]
    files = []
    for target in targets:
        target = target if target.is_absolute() else ROOT / target
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            files.extend(p for p in target.rglob("*") if p.suffix in SUFFIXES)
    bad = 0
    for path in sorted(files):
        for number, text in check(path):
            bad += 1
            print(f"{path.relative_to(ROOT)}:{number}: odd number of quotes: {text}")
    print(f"checked {len(files)} file(s), {bad} suspicious line(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
