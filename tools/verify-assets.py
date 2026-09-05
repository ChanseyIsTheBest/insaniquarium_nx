#!/usr/bin/env python3
"""
verify-assets.py -- check a copy of the game's data against resources.xml.

The Switch filesystem is case-sensitive and Windows is not, so a retail copy
usually has a handful of files whose case does not match what resources.xml
asks for. On Windows the game does not care; here each one is a missing
resource, and the failure arrives as an unexplained crash during loading rather
than as anything that names the file.

    python3 tools/verify-assets.py /path/to/game
    python3 tools/verify-assets.py /path/to/game --fix-case

--fix-case renames files whose name matches case-insensitively but not exactly.
It also handles the alpha-channel companions ("_" suffix) that resources.xml
never mentions by name but the loader looks for alongside each image.
"""
import argparse
import os
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

# resources.xml gives paths without extensions for images and sounds; the
# loader tries these in order.
IMAGE_EXTS = [".jpg", ".png", ".gif", ".tga", ".j2k"]
SOUND_EXTS = [".wav", ".ogg", ".mp3", ".u8"]
FONT_EXTS = [".txt"]


def index_tree(root: pathlib.Path):
    """Map lowercased relative path -> actual relative path, for the whole tree."""
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = pathlib.Path(dirpath) / name
            rel = full.relative_to(root)
            out[str(rel).replace("\\", "/").lower()] = str(rel).replace("\\", "/")
    return out


def wanted_paths(resources_xml: pathlib.Path):
    """Every path resources.xml refers to, with the extensions it might have."""
    try:
        tree = ET.parse(resources_xml)
    except ET.ParseError as e:
        print(f"could not parse {resources_xml}: {e}")
        return []

    out = []
    for el in tree.iter():
        path = el.get("path")
        if not path:
            continue
        path = path.replace("\\", "/")
        tag = el.tag.lower()
        if tag == "image":
            exts = IMAGE_EXTS
        elif tag == "sound":
            exts = SOUND_EXTS
        elif tag == "font":
            exts = FONT_EXTS
        else:
            exts = [""]
        out.append((path, exts, tag))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gamedir", help="folder holding properties/, images/, sounds/ ...")
    ap.add_argument("--fix-case", action="store_true",
                    help="rename files whose case does not match resources.xml")
    args = ap.parse_args()

    root = pathlib.Path(args.gamedir).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}")
        return 2

    res = root / "properties" / "resources.xml"
    if not res.is_file():
        print(f"missing {res}")
        print("This is the file the port looks for to identify a game folder,")
        print("so without it the game will not find its data at all.")
        return 2

    index = index_tree(root)
    entries = wanted_paths(res)
    print(f"{len(entries)} resources referenced by resources.xml")

    missing = []
    mismatched = []   # (wanted, actual)

    for path, exts, _tag in entries:
        found_exact = False
        found_ci = None
        for ext in exts:
            candidate = (path + ext).lstrip("/")
            key = candidate.lower()
            if key in index:
                if index[key] == candidate:
                    found_exact = True
                    break
                found_ci = (candidate, index[key])
        if found_exact:
            continue
        if found_ci:
            mismatched.append(found_ci)
        else:
            missing.append(path)

    # Alpha companions: for image "foo.jpg" the loader also looks for "foo_.jpg".
    for wanted, actual in list(mismatched):
        stem, ext = os.path.splitext(wanted)
        alpha = stem + "_" + ext
        key = alpha.lower()
        if key in index and index[key] != alpha:
            mismatched.append((alpha, index[key]))

    print()
    if mismatched:
        print(f"CASE MISMATCH ({len(mismatched)}):")
        for wanted, actual in mismatched[:40]:
            print(f"   want {wanted}")
            print(f"   have {actual}")
        if len(mismatched) > 40:
            print(f"   ... and {len(mismatched) - 40} more")
    else:
        print("case: ok")

    print()
    if missing:
        print(f"MISSING ({len(missing)}):")
        for path in missing[:40]:
            print(f"   {path}")
        if len(missing) > 40:
            print(f"   ... and {len(missing) - 40} more")
        print()
        print("Missing files are usually a folder that was not copied across.")
    else:
        print("missing: none")

    if args.fix_case and mismatched:
        print()
        print("renaming...")
        done = 0
        for wanted, actual in mismatched:
            src = root / actual
            dst = root / wanted
            if not src.exists() or src == dst:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            # Two-step, because a case-only rename is a no-op on a
            # case-insensitive host filesystem (which is often where the files
            # are sitting when this script gets run).
            tmp = src.with_name(src.name + ".casefix")
            src.rename(tmp)
            tmp.rename(dst)
            done += 1
        print(f"   renamed {done}")

    return 1 if (missing or (mismatched and not args.fix_case)) else 0


if __name__ == "__main__":
    sys.exit(main())
