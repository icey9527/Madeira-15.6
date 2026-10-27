#!/usr/bin/env python3
"""Extract user-facing UI strings from Madeira's SwiftUI sources.

The app has no .strings/.xcstrings catalogue: every label is a literal in the
Swift source.  Building one is mechanical, so this tool does it:

    extract   write a translation catalogue (original -> translation)
    apply     rewrite the Swift sources using a filled-in catalogue

The catalogue is a JSON object mapping the exact source literal to its
translation.  Translation must keep every Swift interpolation (``\\(name)``)
and escape sequence intact, in the same order.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# A Swift string literal body: anything, with escapes, but not an unescaped quote.
STRING = r'"((?:[^"\\]|\\.)*)"'

# SwiftUI/UIKit entry points whose first argument is shown to the user.
LABELS = (
    "Text", "Label", "Button", "Toggle", "Section", "TextField", "SecureField",
    "Picker", "Stepper", "Link", "NavigationLink", "Menu", "navigationTitle",
    "navigationBarTitle", "alert", "confirmationDialog", "accessibilityLabel",
    "accessibilityHint", "tabItem", "badge", "help",
    # Our own iOS 15 wrappers and in-repo helpers (see Library.swift / Onboarding.swift).
    "LabeledContent15", "EmptyStateView", "steamActionLabel", "mbPicker",
    "launchGlyph", "header", "section", "primary", "secondary",
    # UIKit setters and notification helpers that take the visible text first.
    "setTitle", "updateTitle", "notify",
)
PATTERN = re.compile(r'\b(' + "|".join(LABELS) + r')\s*\(\s*' + STRING)

# Constructors whose visible text is a labelled argument, not the first argument.
# Kept as an explicit allow-list so ordinary `title:`/`message:` metadata is never
# mistaken for UI text (e.g. ConfigCatalog's generated option titles).
LABELLED_CONSTRUCTORS = (
    "UIAlertController", "UIAction", "UIBarButtonItem", "UIAlertAction",
    "BGContinuedProcessingTaskRequest",
)
LABELLED_KEYS = ("title", "message", "subtitle")
PATTERN_LABELLED = re.compile(
    r'\b(' + "|".join(LABELLED_CONSTRUCTORS) + r')\s*\(\s*'
    r'(?:' + "|".join(LABELLED_KEYS) + r')\s*:\s*' + STRING,
)
# `Text(verbatim: "...")` shows its argument as-is.
PATTERN_VERBATIM = re.compile(r'(\bText\s*\(\s*verbatim\s*:)\s*' + STRING)
# `notify("title", body: "...")` / `updateTitle("...", subtitle: "...")`: the
# trailing parts are visible too, but only on the helper call itself.
PATTERN_TAIL = re.compile(
    r'(\b(?:notify|updateTitle)\s*\([^\n]*?\b(?:body|subtitle)\s*:)\s*' + STRING,
)

# Deliberately no "cond ? \"A\" : \"B\"" rule: string literals nested inside a
# \\(…) interpolation (pluralisation, " · newer") look identical and translating
# a fragment would corrupt the sentence.
PATTERNS = (PATTERN, PATTERN_LABELLED, PATTERN_VERBATIM, PATTERN_TAIL)


def iter_literals(root: Path):
    for path in sorted(root.rglob("*.swift")):
        text = path.read_text(encoding="utf-8")
        for pattern in PATTERNS:
            for match in pattern.finditer(text):
                # group 1 is the call/prefix, group 2 is the literal body.
                for index in range(2, pattern.groups + 1):
                    literal = match.group(index)
                    if literal is not None:
                        yield path, literal


def load_catalogue(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("catalogue must be a JSON object")
    return data


# A Swift interpolation is "\\(" ... ")" with balanced ASCII parentheses inside.
FULLWIDTH_PARENS = "\uFF08\uFF09"  # （ ）


def extract_interpolations(text: str) -> tuple[list[str], str | None]:
    """Return every \\(...) interpolation with balanced parens, or an error."""
    out: list[str] = []
    i = 0
    while True:
        start = text.find("\\(", i)
        if start < 0:
            return out, None
        depth = 0
        k = start + 1  # points at the "("
        while k < len(text):
            ch = text[k]
            if ch in FULLWIDTH_PARENS:
                return out, (
                    "full-width parenthesis inside interpolation; "
                    f"near {text[start:start + 40]!r}"
                )
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    out.append(text[start : k + 1])
                    break
            elif ch == "\n":
                return out, f"newline inside interpolation near {text[start:start + 40]!r}"
            k += 1
        else:
            return out, f"unbalanced interpolation {text[start:start + 40]!r}"
        i = k + 1


def check_placeholders(original: str, translated: str) -> str | None:
    want, want_err = extract_interpolations(original)
    have, have_err = extract_interpolations(translated)

    if want_err:
        # The extractor stops at the first unescaped quote, so a literal whose
        # interpolation embeds a nested string (e.g. \\(a ? "" : "b")) comes
        # back as a prefix: the tail after the last "\\(" is still an
        # incomplete interpolation.  The replacer swaps this prefix in place, so
        # the translation must keep the same number of interpolations and the
        # same trailing fragment verbatim.
        if original.count("\\(") != translated.count("\\("):
            return f"interpolation count changed: {original.count(chr(92)+chr(40))} -> {translated.count(chr(92)+chr(40))}"
        if original.rsplit("\\(", 1)[-1] != translated.rsplit("\\(", 1)[-1]:
            return (
                "trailing interpolation fragment changed: "
                f"{original.rsplit(chr(92)+chr(40), 1)[-1]!r} -> {translated.rsplit(chr(92)+chr(40), 1)[-1]!r}"
            )
        return None

    if have_err:
        return f"translation is malformed: {have_err}"
    if want != have:
        return f"placeholders changed: {want} -> {have}"
    return None


def command_extract(args: argparse.Namespace) -> int:
    catalogue: dict[str, str] = {}
    for _path, literal in iter_literals(args.source):
        catalogue.setdefault(literal, "")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(catalogue, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(catalogue)} strings to {args.output}")
    return 0


def command_apply(args: argparse.Namespace) -> int:
    catalogue = load_catalogue(args.catalogue)
    translated = {k: v for k, v in catalogue.items() if v.strip()}
    if not translated:
        raise SystemExit("catalogue has no translated entries")

    problems: list[str] = []
    for source, target in translated.items():
        issue = check_placeholders(source, target)
        if issue:
            problems.append(f"{source!r}: {issue}")
    if problems:
        for problem in problems[:30]:
            print("placeholder mismatch: " + problem, file=sys.stderr)
        return 1

    total = 0
    replaced = 0
    for path in sorted(args.source.rglob("*.swift")):
        original = path.read_text(encoding="utf-8")

        def substitute(match: re.Match[str]) -> str:
            nonlocal replaced
            # Replace exactly the literal body (group 2), keeping its quotes and
            # any prefix such as `Text(verbatim:`, `title: ` or
            # `notify("…", body: `.
            base = match.start(0)
            text = match.group(0)
            edits: list[tuple[int, int, str]] = []
            for index in range(2, match.re.groups + 1):
                literal = match.group(index)
                target = translated.get(literal) if literal is not None else None
                if target is None:
                    continue
                start, end = match.span(index)
                edits.append((start - base, end - base, target))
                replaced += 1
            for start, end, replacement in sorted(edits, reverse=True):
                text = text[:start] + replacement + text[end:]
            return text

        updated = original
        for pattern in PATTERNS:
            updated = pattern.sub(substitute, updated)
        if updated != original:
            if args.backup:
                shutil.copyfile(path, path.with_suffix(path.suffix + ".orig"))
            path.write_text(updated, encoding="utf-8")
            total += 1
            print(f"updated {path.relative_to(args.source)}")
    print(f"rewrote {total} files ({replaced} occurrences)")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract", help="write a translation catalogue")
    extract.add_argument("--source", type=Path, default=Path("app/Madeira"))
    extract.add_argument("--output", type=Path, default=Path("localization/strings.json"))
    extract.set_defaults(func=command_extract)

    apply_cmd = sub.add_parser("apply", help="rewrite sources from a catalogue")
    apply_cmd.add_argument("--source", type=Path, default=Path("app/Madeira"))
    apply_cmd.add_argument("--catalogue", type=Path, default=Path("localization/strings.json"))
    apply_cmd.add_argument("--backup", action="store_true", help="keep a .orig copy of each rewritten file")
    apply_cmd.set_defaults(func=command_apply)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
