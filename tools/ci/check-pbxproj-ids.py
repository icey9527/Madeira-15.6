"""Fail when two objects in project.pbxproj share an id (they silently eat each other)."""
import collections
import pathlib
import re
import sys

text = pathlib.Path("app/Madeira.xcodeproj/project.pbxproj").read_text(encoding="utf-8")
defs = re.findall(r"^\t\t([A-F0-9]{8}) /\* .*? \*/ = \{isa = ", text, re.M)
dupes = [i for i, n in collections.Counter(defs).items() if n > 1]
for i in dupes:
    print("duplicate object id:", i)
print(f"{len(defs)} objects, {len(dupes)} duplicate id(s)")
sys.exit(1 if dupes else 0)
