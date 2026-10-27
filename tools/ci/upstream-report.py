#!/usr/bin/env python3
"""What has everyone else already done that this branch does not have?

Three questions come up every time something breaks here:

  1. Which commits does upstream (and the other forks) have that we lack?
  2. Which open pull requests touch the area I am debugging?
  3. Has anyone already fixed this exact thing?

`git log` answers all three, but only if you remember the right ref, the right
range and the right grep every time. This wraps them:

    tools/ci/upstream-report.py                 # summary per remote
    tools/ci/upstream-report.py --search adpcm  # commits touching that word
    tools/ci/upstream-report.py --prs           # open PRs (needs a token)
    tools/ci/upstream-report.py --fetch         # git fetch the remotes first

The PR list uses the GitHub API with a token from $GH_TOKEN, or from
docs/gh_token.txt (the file this repository keeps locally; it is never
printed). Without a token only the git-side report runs.
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Remotes worth comparing against. A remote that is not configured is skipped
# with a note instead of failing: not every checkout has the forks added.
REMOTES = ("upstream", "iediot")

# Where a commit's files land decides which part of the build it invalidates.
AREAS = (
    ("app (Swift/ObjC)", ("app/",)),
    ("dxmt", ("dxmt/", "build/dxmt-ios/")),
    ("ntdll", ("build/ntdll-unix/",)),
    ("wineserver", ("build/wineserver/",)),
    ("win32u", ("build/win32u-unix/",)),
    ("wine pe/i386", ("build/wine-", "build/wine/")),
    ("d3d12", ("madeira-d3d12/",)),
    ("ci/tools", (".github/", "tools/")),
)


def git(*args, check=True):
    result = subprocess.run(("git",) + args, cwd=ROOT, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def remote_exists(remote):
    return bool(git("remote", check=False).split().count(remote))


def default_branch(remote):
    out = git("symbolic-ref", "-q", f"refs/remotes/{remote}/HEAD", check=False).strip()
    if out:
        return out.rsplit("/", 1)[-1]
    for name in ("main", "master"):
        if git("rev-parse", "--verify", "-q", f"{remote}/{name}", check=False):
            return name
    return None


def area_of(paths):
    for name, prefixes in AREAS:
        if any(p.startswith(prefixes) for p in paths):
            return name
    return "other"


def commits_behind(our, remote_ref):
    """Commits in remote_ref that our branch does not have, grouped by area."""
    shas = git("rev-list", f"{our}..{remote_ref}").split()
    if not shas:
        return {}, 0
    out = git("log", "--no-walk=unsorted", "--format=@@%h%s", "--name-only", *shas)
    groups, total = {}, 0
    sha = subject = None
    for line in out.splitlines():
        if line.startswith("@@"):
            if sha:
                groups.setdefault(area_of(files), []).append((sha, subject))
                total += 1
            head = line[2:]
            sha, _, subject = head.partition("")
            files = []
        elif line.strip() and sha:
            files.append(line.strip())
    if sha:
        groups.setdefault(area_of(files), []).append((sha, subject))
        total += 1
    return groups, total


def search(term, refs):
    """Commits whose subject/body or patch mentions the term."""
    seen, hits = set(), []
    for ref in refs:
        out = git("log", "--format=%h%x1f%ad%x1f%s", "--date=short", "-i",
                  f"--grep={term}", f"--all-match", ref, check=False)
        for line in out.splitlines():
            sha, _, rest = line.partition("\x1f")
            if sha in seen:
                continue
            seen.add(sha)
            date, _, subject = rest.partition("\x1f")
            hits.append((ref, sha, date, subject))
    return hits


def token():
    value = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if value:
        return value.strip()
    for path in (ROOT / "docs/gh_token.txt", ROOT.parent / "docs/gh_token.txt"):
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return None


def open_prs(repo, limit=100):
    tok = token()
    if not tok:
        print("(no token: set GH_TOKEN or keep docs/gh_token.txt to list PRs)")
        return []
    url = f"https://api.github.com/repos/{repo}/pulls?state=open&per_page={limit}"
    request = urllib.request.Request(url, headers={
        "Authorization": f"token {tok}",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except Exception as error:  # noqa: BLE001 - a report tool should not crash
        print(f"(could not list PRs for {repo}: {error})")
        return []


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--our", default="HEAD", help="our branch (default HEAD)")
    parser.add_argument("--fetch", action="store_true", help="git fetch the remotes first")
    parser.add_argument("--search", metavar="TERM", help="find commits mentioning TERM")
    parser.add_argument("--prs", action="store_true", help="list open PRs of the remotes")
    parser.add_argument("--all", action="store_true", help="search every remote-tracking ref")
    parser.add_argument("--limit", type=int, default=12, help="commits listed per area")
    args = parser.parse_args()

    if args.fetch:
        for remote in REMOTES:
            if remote_exists(remote):
                print(f"fetching {remote}...")
                git("fetch", "--no-recurse-submodules", "-q", remote, check=False)

    refs = []
    for remote in REMOTES:
        if not remote_exists(remote):
            print(f"({remote}: not configured, skipped)")
            continue
        branch = default_branch(remote)
        if not branch:
            continue
        refs.append((remote, f"{remote}/{branch}"))

    if args.search:
        scope = ["--all"] if args.all else refs
        targets = ["--all"] if args.all else [ref for _, ref in refs]
        hits = search(args.search, targets)
        print(f"commits mentioning {args.search!r}: {len(hits)}")
        for ref, sha, date, subject in hits[:args.limit * 4]:
            print(f"  {sha} {date} [{ref}] {subject[:110]}")
        return

    for remote, ref in refs:
        groups, total = commits_behind(args.our, ref)
        print(f"\n=== {ref}: {total} commit(s) we lack ===")
        for area in sorted(groups, key=lambda a: -len(groups[a])):
            print(f"  {area}: {len(groups[area])}")
            for sha, subject in groups[area][:args.limit]:
                print(f"    {sha} {subject[:105]}")

    if args.prs:
        for repo in ("willfaust/Madeira", "iediot/Madeira"):
            pulls = open_prs(repo)
            print(f"\n=== {repo}: {len(pulls)} open PR(s) ===")
            for pr in pulls:
                print(f"  #{pr['number']} {pr['title'][:100]}")


if __name__ == "__main__":
    sys.exit(main())
