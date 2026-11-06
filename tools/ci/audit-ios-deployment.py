"""Find newer-than-15.6 API call sites with no #available guard nearby."""
import pathlib
import re

RULES = [
    ("onChange two-param", r"\.onChange\(of:[^)]*\)\s*\{\s*_,"),
    ("URL.documentsDirectory", r"URL\.documentsDirectory"),
    ("persistentSystemOverlays", r"\.persistentSystemOverlays\("),
    ("NavigationStack", r"\bNavigationStack\b"),
    ("navigationDestination", r"\.navigationDestination\("),
    ("Duration", r":\s*Duration\b|\bDuration\s*="),
    ("OSAllocatedUnfairLock", r"\bOSAllocatedUnfairLock\b"),
    ("ProposedViewSize", r"\bProposedViewSize\b"),
    ("springDuration", r"springDuration:"),
    ("UIHostingConfiguration", r"\bUIHostingConfiguration\b"),
    ("registerForTraitChanges", r"\bregisterForTraitChanges\("),
    ("traitOverrides", r"\.traitOverrides"),
    ("safeAreaRegions", r"\.safeAreaRegions"),
    ("UITraitPreferredContentSizeCategory", r"\bUITraitPreferredContentSizeCategory\b"),
    ("UITraitUserInterfaceStyle", r"\bUITraitUserInterfaceStyle\b"),
    ("ContentUnavailableView", r"\bContentUnavailableView\b"),
    ("ShareLink", r"\bShareLink\b"),
    ("LabeledContent", r"\bLabeledContent\b"),
    ("Grid", r"\bGrid\s*\{"),
    ("onGeometryChange", r"\.onGeometryChange\("),
    ("scrollTargetBehavior", r"\.scrollTargetBehavior\("),
    ("presentationDetents", r"\.presentationDetents\("),
    ("Task.sleep(for:)", r"Task\.sleep\(for:"),
    ("ViewThatFits", r"ViewThatFits"),
    ("pickerStyle(.navigationLink)", r"\.pickerStyle\(\.navigationLink\)"),
    ("scrollContentBackground", r"\.scrollContentBackground\("),
]

root = pathlib.Path("app/Madeira")
hits = []
for path in sorted(root.rglob("*.swift")):
    lines = path.read_text(encoding="utf-8").split("\n")
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith(("//", "*", "/*")):
            continue
        for name, pattern in RULES:
            if re.search(pattern, line):
                context = "\n".join(lines[max(0, number - 8):number + 2])
                if "available(iOS" in context:
                    continue
                hits.append((str(path), number, name, stripped[:78]))

for path, number, name, text in hits:
    print(f"{path}:{number}: {name} | {text}")
print(f"{len(hits)} unguarded site(s)")
