#!/usr/bin/env python3
"""
check-leftovers.py -- report Win32 code that is NOT inside #ifdef _WIN32.

A plain grep is useless here: after the fixups run, the screensaver installer
and the registry calls are still in the file, correctly wrapped in
#ifdef _WIN32. Grep reports all of them and the real problems get lost in the
noise, which trains you to ignore the output.

This tracks preprocessor nesting and only reports hits that would actually be
compiled on this platform. Anything it prints is unfinished work.
"""
import pathlib
import re
import sys

DST = pathlib.Path(__file__).resolve().parent.parent / "game"

# Excluded from the build in CMakeLists.txt, so its contents do not matter.
SKIP_FILES = {"BetaSupport.cpp", "BetaSupport.h",
              "Beetlemuncher.cpp", "ColorUtils.cpp"}

PATTERNS = [
    (r"\bWinMain\b|\bHINSTANCE\b",                    "Win32 entry point"),
    (r"#include\s*<windows",                          "windows.h"),
    (r"#include\s*<process\.h>",                      "process.h"),
    (r"#include\s*<direct\.h>",                       "direct.h"),
    (r"\bRegOpenKey|\bRegQueryValue|\bRegSetValue|\bRegCloseKey", "registry"),
    (r"\bSystemParametersInfo",                       "SystemParametersInfo"),
    (r"SEHCatcher::m",                                "SEHCatcher statics"),
    (r"\bDDImage\b|\bDDInterface\b|\bD3DInterface\b",  "DirectDraw / Direct3D"),
    (r"\bBassMusicInterface\b",                       "BASS music"),
    (r"\bCreateFontA\b|\bGetDesktopWindow\b|\bDeleteObject\b", "GDI"),
]

IF_WIN32 = re.compile(r"^\s*#\s*if(def)?\s+.*\b_WIN32\b")
IF_NOT_WIN32 = re.compile(r"^\s*#\s*if\s*!\s*defined\s*\(\s*_WIN32|^\s*#\s*ifndef\s+_WIN32")
IF_ANY = re.compile(r"^\s*#\s*if(def|ndef)?\b")
# #elif has to be handled explicitly. Without it the checker silently treats a
# #elif defined(_WIN32) branch as unguarded and reports everything inside it --
# or, worse, the reverse. A verification tool that quietly gets this wrong is
# more dangerous than no tool, because the clean run is what you trust.
ELIF_WIN32 = re.compile(r"^\s*#\s*elif\b.*\b_WIN32\b")
ELIF_ANY = re.compile(r"^\s*#\s*elif\b")
ELSE_ = re.compile(r"^\s*#\s*else\b")
ENDIF = re.compile(r"^\s*#\s*endif\b")


def win32_only_regions(lines):
    """Yield a bool per line: True if it only compiles when _WIN32 is defined."""
    stack = []            # each entry: True (in win32 branch), False, or None
    for line in lines:
        # order matters: #elif must be tested before the generic #if
        if ELIF_WIN32.match(line):
            if stack:
                stack[-1] = True
            yield any(x is True for x in stack)
            continue
        if ELIF_ANY.match(line):
            if stack:
                stack[-1] = None
            yield any(x is True for x in stack)
            continue
        if IF_WIN32.match(line):
            stack.append(True)
            yield any(x is True for x in stack)
            continue
        if IF_NOT_WIN32.match(line):
            stack.append(False)
            yield any(x is True for x in stack)
            continue
        if IF_ANY.match(line):
            stack.append(None)
            yield any(x is True for x in stack)
            continue
        if ELSE_.match(line):
            if stack:
                top = stack[-1]
                # after an #else the branch is win32-only only if we can prove
                # the preceding branches were all non-win32
                stack[-1] = (False if top is True else
                             True if top is False else None)
            yield any(x is True for x in stack)
            continue
        if ENDIF.match(line):
            if stack:
                stack.pop()
            yield any(x is True for x in stack)
            continue
        yield any(x is True for x in stack)


def main():
    if not DST.exists():
        print(f"missing {DST} -- run port.sh first")
        return 1

    compiled = [(re.compile(p), d) for p, d in PATTERNS]
    findings = {}

    for path in sorted(list(DST.glob("*.cpp")) + list(DST.glob("*.h"))):
        if path.name in SKIP_FILES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        guarded = list(win32_only_regions(text))
        for i, line in enumerate(text):
            if guarded[i]:
                continue                      # inside #ifdef _WIN32, fine
            # Comments explaining what was removed naturally name the thing
            # they removed. Matching those would mean writing the comments
            # around the checker, which is backwards.
            stripped = line.lstrip()
            if (stripped.startswith("//") or stripped.startswith("*")
                    or stripped.startswith("/*")):
                continue
            for rx, desc in compiled:
                if rx.search(line):
                    findings.setdefault(desc, []).append(
                        (path.name, i + 1, line.strip()[:90]))

    if not findings:
        print("   ok, nothing Win32 outside #ifdef _WIN32")
        return 0

    for desc, hits in findings.items():
        print(f"\n   --- {desc}")
        for name, ln, txt in hits[:10]:
            print(f"      {name}:{ln}: {txt}")
        if len(hits) > 10:
            print(f"      ... and {len(hits) - 10} more")
    print()
    print("   These would be compiled on Switch. Each needs a shim in")
    print("   compat/winfish_compat.h or a fixup in port/fixups/.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
