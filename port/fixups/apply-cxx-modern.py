#!/usr/bin/env python3
"""
apply-cxx-modern.py -- things a 2026 GCC rejects that MSVC 2005 accepted, plus
the 64-bit bugs that fall out of compiling 32-bit code for aarch64.

Kept separate from the other two fixups on purpose. apply-framework.py maps one
framework onto another and apply-portability.py removes Windows-only code;
neither of those is what this is. These changes would be needed even if the
target were 64-bit Windows.

Two of them are not compile errors at all -- they are latent bugs that the
original never hit because it was a 32-bit build:

  * `sizeof(mCheatCodes) / 4` counts an array of pointers by assuming pointers
    are 4 bytes. On aarch64 they are 8, so the loop runs 16 times over an
    8-element array and dereferences whatever follows it. GCC spots this
    ("iteration 8 invokes undefined behavior") but only warns.

  * `ulong* bits = image->GetBits()` reads the pixel buffer through a 64-bit
    pointer. Even once the compiler is satisfied, the stride would be wrong --
    8 bytes per pixel instead of 4 -- so every image touched this way would be
    read at half resolution and run off the end of the buffer.

Every replacement is verified. A pattern that stops matching reports [FAILED]
and the run stops.
"""
import pathlib
import re
import sys

DST = pathlib.Path(__file__).resolve().parent.parent / "game"


def crlf(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def sub(path, old, new, label, count=1):
    p = DST / path
    if not p.exists():
        print(f"   [MISSING FILE] {label}  ({path})")
        return False
    data = p.read_bytes()
    # Choose the line-ending variant from the FILE, not from match order. For a
    # single-line pattern crlf(old) == old, so the CRLF variant always matched
    # first and wrote a CRLF replacement into LF files -- three stray CRLF lines
    # in Board.cpp. Harmless to the compiler, but a file should have one ending.
    is_crlf = b"\r\n" in data
    variants = ((crlf(old), crlf(new)), (old, new)) if is_crlf else ((old, new),)
    for o, n in variants:
        if o in data:
            p.write_bytes(data.replace(o, n, count))
            print(f"   [ok] {label}")
            return True
    for _, n in ((crlf(old), crlf(new)), (old, new)):
        if n in data:
            print(f"   [already applied] {label}")
            return True
    print(f"   [FAILED] {label}  ({path})")
    return False


def regex_all(pattern, repl, label, files=None):
    """Apply a regex across the game sources. Reports how many sites changed."""
    rx = re.compile(pattern)
    total = 0
    touched = []
    targets = ([DST / f for f in files] if files
               else sorted(list(DST.glob("*.cpp")) + list(DST.glob("*.h"))))
    for p in targets:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        new, n = rx.subn(repl, text)
        if n:
            p.write_text(new, encoding="utf-8", newline="")
            total += n
            touched.append(f"{p.name}({n})")
    if total:
        print(f"   [ok] {label}: {total} site(s) -- {', '.join(touched)}")
    else:
        print(f"   [none] {label}")
    return True


def main():
    ok = True

    # --- 1. pixel buffers are 32-bit ---------------------------------------
    # MemoryImage::GetBits() returns uint32_t*. The decomp declares ulong*,
    # which was the same width on 32-bit Windows and is not here.
    #
    # Scoped to the four files that actually walk pixel buffers. Every ulong*
    # in them is an image pointer -- either straight from GetBits() or offset
    # from one -- so converting the declarations wholesale is right, and
    # catches the derived pointers (`ulong* p = aBGBits + ...`) that a
    # GetBits-only pattern would miss. Scalar `ulong` offsets are left alone:
    # they are indices, not pointers, and 64 bits does them no harm.
    print("== pixel buffer pointers ==")
    ok &= regex_all(r"\bulong\*(\s+\w+\s*=)",
                    r"uint32_t*\1",
                    "ulong* -> uint32_t* on image data",
                    files=["BonusScreen.cpp", "GameObject.cpp",
                           "GameSelector.cpp", "WinFishCommon.cpp"])

    # --- 2. temporaries bound to non-const references ----------------------
    # MSVC allowed SetColorHelper(g, Color(...)). GCC does not, and
    # -fpermissive does not cover it. The function only reads theColor, so
    # const& is the correct signature rather than a workaround.
    print("== non-const reference parameters ==")
    ok &= sub("GameObject.h",
              b"SetColorHelper(Graphics* g, Color& theColor);",
              b"SetColorHelper(Graphics* g, const Color& theColor);",
              "GameObject.h: SetColorHelper takes const Color&")
    ok &= sub("GameObject.cpp",
              b"void Sexy::GameObject::SetColorHelper(Graphics* g, Color& theColor)",
              b"void Sexy::GameObject::SetColorHelper(Graphics* g, const Color& theColor)",
              "GameObject.cpp: SetColorHelper takes const Color&")

    # Same class of problem, found by scanning for every function taking a
    # non-const Color&/Rect& that is called with a constructor temporary rather
    # than by waiting for the compiler to report them one file at a time.
    # All three only read theRect.
    # Making the three DrawInstr* functions const propagates: their bodies
    # hand theRect to DrawInstrText, which also declared it non-const. It only
    # reads the rect's members and passes nothing on, so the cascade stops
    # here. Listed separately from the loop below because its signature has
    # more parameters.
    ok &= sub("HelpScreen.h",
              b"DrawInstrText(Graphics* g, Rect& theRect,",
              b"DrawInstrText(Graphics* g, const Rect& theRect,",
              "HelpScreen.h: DrawInstrText takes const Rect&")
    ok &= sub("HelpScreen.cpp",
              b"void Sexy::HelpScreen::DrawInstrText(Graphics* g, Rect& theRect,",
              b"void Sexy::HelpScreen::DrawInstrText(Graphics* g, const Rect& theRect,",
              "HelpScreen.cpp: DrawInstrText takes const Rect&")

    for fn in ("DrawInstrLeftPart", "DrawInstrMiddlePart", "DrawInstrRightPart"):
        ok &= sub("HelpScreen.h",
                  f"{fn}(Graphics* g, Rect& theRect);".encode(),
                  f"{fn}(Graphics* g, const Rect& theRect);".encode(),
                  f"HelpScreen.h: {fn} takes const Rect&")
        ok &= sub("HelpScreen.cpp",
                  f"void Sexy::HelpScreen::{fn}(Graphics* g, Rect& theRect)".encode(),
                  f"void Sexy::HelpScreen::{fn}(Graphics* g, const Rect& theRect)".encode(),
                  f"HelpScreen.cpp: {fn} takes const Rect&")

    # --- 3. missing includes ------------------------------------------------
    # Both worked on MSVC only because some other header happened to be
    # included first. Include order is not a contract.
    print("== includes the decomp relied on arriving transitively ==")
    ok &= sub("WinFishCommon.h",
              b'#include "SexyAppFramework/MemoryImage.h"',
              b'#include "SexyAppFramework/MemoryImage.h"\n#include "SexyAppFramework/Font.h"',
              "WinFishCommon.h: Font.h (Font* used in two prototypes)")

    # The same problem, generalised. 2005 MSVC pulled most of the standard
    # library in through its own headers, so the decomp names std::list,
    # std::map, std::set and std::vector in a dozen places without ever
    # including them. GCC 16 does not, and each one is a hard error. Fixing
    # them by hand as the compiler finds them would take a dozen rebuilds, so
    # every file is scanned instead.
    print("== missing standard library headers ==")
    added = []
    for p in sorted(list(DST.glob("*.h")) + list(DST.glob("*.cpp"))):
        text = p.read_text(encoding="utf-8", errors="ignore")
        want = []
        for hdr in ("vector", "list", "map", "set", "deque", "string"):
            if re.search(r"\bstd::" + hdr + r"\s*<", text) and \
               not re.search(r"#\s*include\s*<" + hdr + r">", text):
                want.append(hdr)
        if not want:
            continue

        lines = text.split("\n")
        # after the include guard's #define if there is one, else at the top,
        # so the new includes cannot land outside the guard
        idx = 0
        for i, line in enumerate(lines[:10]):
            if re.match(r"\s*#\s*define\s+__\w+__", line):
                idx = i + 1
                break
        block = [""] + [f"#include <{h}>" for h in want]
        lines[idx:idx] = block
        p.write_text("\n".join(lines), encoding="utf-8", newline="")
        added.append(f"{p.name}<{','.join(want)}>")

    if added:
        print(f"   [ok] added to {len(added)} file(s):")
        for a in added:
            print(f"      {a}")
    else:
        print("   [none] all standard headers already included")

    # GetPetName returns SexyString BY VALUE, so binding the result to a
    # non-const reference binds to a temporary. MSVC allowed it. Taking a copy
    # rather than a const& because the semantics are identical here -- the
    # original bound to a temporary, so any write was discarded anyway -- and a
    # value is harder to get wrong later.
    ok &= sub("PetsScreen.cpp",
              b"SexyString& aPetName = GetPetName(thePetId);",
              b"SexyString aPetName = GetPetName(thePetId);",
              "PetsScreen.cpp: aPetName is a copy, not a reference to a temporary")

    # HTTPTransfer is only forward-declared here, but InternetManager has
    # HTTPTransfer *members*, which need the complete type. It built on MSVC
    # because InternetManager.cpp includes HTTPTransfer.h before this header;
    # SexyApp.cpp includes it without, and fails.
    #
    # The declaration sits INSIDE `namespace Sexy {`, so the namespace opening
    # has to be part of the match -- an #include cannot go inside it, and
    # replacing the declaration alone would leave the namespace opened twice.
    ok &= sub("InternetManager.h",
              b"namespace Sexy\n{\n\tclass HTTPTransfer;",
              b'#include "SexyAppFramework/HTTPTransfer.h"\n\nnamespace Sexy\n{',
              "InternetManager.h: HTTPTransfer.h (has members of that type)")

    # --- 4. ambiguous ButtonListener ---------------------------------------
    # The framework declares `class Dialog : public Widget, public
    # ButtonListener`. Anything deriving from Dialog (or from MoneyDialog,
    # which derives from Dialog) and ALSO naming ButtonListener inherits it
    # twice, so `this` cannot convert to ButtonListener* unambiguously.
    #
    # Classes deriving straight from Widget are untouched: Widget does not
    # inherit ButtonListener, so naming it there is correct and necessary.
    print("== ambiguous ButtonListener bases ==")
    fixed = []
    for p in sorted(DST.glob("*.h")):
        text = p.read_text(encoding="utf-8", errors="ignore")
        out = []
        changed = 0
        for line in text.split("\n"):
            if (re.search(r"\bclass\s+\w+\s*:", line)
                    and re.search(r"public\s+(Dialog|MoneyDialog)\b", line)
                    and "public ButtonListener" in line):
                line = re.sub(r",\s*public\s+ButtonListener\b", "", line)
                changed += 1
            out.append(line)
        if changed:
            p.write_text("\n".join(out), encoding="utf-8", newline="")
            fixed.append(f"{p.name}({changed})")
    if fixed:
        print(f"   [ok] removed redundant base: {', '.join(fixed)}")
    else:
        print("   [none] redundant ButtonListener bases")

    # --- 4b. missing return -------------------------------------------------
    # IsSpecialFishInTank falls off the end when the loop finds nothing, so it
    # returns whatever happens to be in the return register. GCC only warns
    # ("control reaches end of non-void function"). The store uses this to
    # decide whether a special fish is already in the tank, so a stray non-zero
    # makes it behave as though fish are present that are not.
    print("== missing return ==")
    ok &= sub("StoreScreen.cpp",
              b"""\t\tif (anObj->mPreNamedTypeId == theSpecialFishId)
\t\t\treturn true;
\t}
}""",
              b"""\t\tif (anObj->mPreNamedTypeId == theSpecialFishId)
\t\t\treturn true;
\t}
\treturn false;
}""",
              "StoreScreen.cpp: IsSpecialFishInTank returns false when not found")

    # --- 4c. deliberate pointer truncation ----------------------------------
    # GetIdByVariable keys a map on the low 32 bits of whatever gResources[i]
    # points at. That array mixes types -- it holds the ADDRESSES of Image*,
    # _Font* and plain int globals -- so widening the key to uintptr_t would
    # read 8 bytes out of the 4-byte sound globals. The truncation is applied
    # identically on both sides, so it is consistent and correct as written.
    #
    # Casting through uintptr_t first says that out loud and silences the
    # warning, so nobody later "fixes" this into an actual over-read.
    print("== deliberate pointer truncation (do not widen) ==")
    ok &= sub("Res.cpp",
              b"aMap.find((int)theVariable);",
              b"aMap.find((int)(uintptr_t)theVariable);",
              "Res.cpp: explicit truncation in GetIdByVariable")
    ok &= sub("Res.cpp",
              b"return GetIdByVariable((void*)theSound);",
              b"return GetIdByVariable((void*)(uintptr_t)theSound);",
              "Res.cpp: explicit widening in GetIdBySound")

    # --- 5. 64-bit array counting ------------------------------------------
    # Not a compile error. mCheatCodes is CheatCode*[8]; sizeof/4 gives 16 on
    # aarch64, so the loop walks eight entries past the end and calls a method
    # on each. Any keypress would do it.
    print("== save-file serialiser: a long is 32 bits on disk ==")
    # The save format was defined on 32-bit Windows, where the framework's ulong
    # was 4 bytes. On aarch64 it is 8, so ReadLong/WriteLong moved 8 bytes each
    # while every caller that reasons about the stream still assumes 4:
    #
    #   Board.cpp      int aType = aDR->ReadLong();   // consumed 8
    #                  aDR->RollbackBytes(4);          // rewound 4
    #                  anObj->Sync(&theSync);          // re-read mType, 4 bytes late
    #   ProfileMgr.cpp same peek-and-rewind on the version field
    #   Board.cpp      aPos + 4U <= aDW->mMemoryLength // placeholder bounds check
    #
    # Every read after the first rollback is misaligned by four bytes, the values
    # are garbage, and the reader eventually walks off the end of the buffer:
    # DataReaderException from SyncLong inside Board::LoadGame, on the second
    # visit to Adventure mode (the first has no save to read; leaving writes one).
    #
    # Fixing the primitives, rather than each caller, makes every 4-byte
    # assumption correct at once -- and makes the saves this port writes
    # byte-compatible with the original game's.
    ok &= sub("DataSync.cpp",
              b"ulong DataReader::ReadLong()\n{\n    ulong result;\n    ReadBytes(&result, sizeof(result));",
              b"ulong DataReader::ReadLong()\n{\n    uint32_t result;  // 32 bits on disk, see apply-cxx-modern.py\n    ReadBytes(&result, sizeof(result));",
              "DataSync.cpp: ReadLong reads 32 bits")
    ok &= sub("DataSync.cpp",
              b"void DataWriter::WriteLong(ulong theValue)\n{\n    //theValue = LONG_NATIVE_TO_LITTLEE(theValue);\n    WriteBytes(&theValue, sizeof(theValue));",
              b"void DataWriter::WriteLong(ulong theValue)\n{\n    uint32_t aValue = (uint32_t)theValue;  // 32 bits on disk, see apply-cxx-modern.py\n    WriteBytes(&aValue, sizeof(aValue));",
              "DataSync.cpp: WriteLong writes 32 bits")
    # The one write that bypasses the primitives. Board::SyncGameData reserves a
    # 4-byte placeholder with WriteLong(0), counts the objects it writes, then
    # patches the count back with a raw memcpy of sizeof(aNumOfObjects) -- and
    # aNumOfObjects is a ulong, 8 bytes here. That writes the count over the
    # placeholder AND the four bytes after it, which are the first object's
    # mType, leaving them zero. Read back: count 6, first object type 0, and
    # every field after it misaligned.
    #
    # Found by decoding a save from the fixed build: bytes 14..21 read
    # 06 00 00 00 00 00 00 00 -- an 8-byte 6 over a 4-byte slot.
    ok &= sub("Board.cpp",
              b"\t\t\tmemcpy((char*)aDW->mMemoryHandle + aPos, &aNumOfObjects, sizeof(aNumOfObjects));",
              b"\t\t{\n\t\t\tuint32_t aCount32 = (uint32_t)aNumOfObjects;  /* 32 bits on disk, see apply-cxx-modern.py */\n\t\t\tmemcpy((char*)aDW->mMemoryHandle + aPos, &aCount32, sizeof(aCount32));\n\t\t}",
              "Board.cpp: object-count patch-back writes 32 bits")

    # Float goes through a ulong stage in both directions -- 8 bytes here where
    # the format wants 4 -- and WriteFloat's reinterpret_cast<ulong&> on a float
    # reads four bytes past it, which is undefined behaviour. Nothing in the
    # game syncs a float today, so this is latent, but it is the same bug as
    # the other three and just as cheap to close.
    ok &= sub("DataSync.cpp",
              b"    ulong result;\n    ReadBytes(&result, sizeof(result));\n    //result = LONG_LITTLEE_TO_NATIVE(result);\n    return reinterpret_cast<float&>(result);",
              b"    uint32_t result;  // 32 bits on disk, see apply-cxx-modern.py\n    ReadBytes(&result, sizeof(result));\n    //result = LONG_LITTLEE_TO_NATIVE(result);\n    return reinterpret_cast<float&>(result);",
              "DataSync.cpp: ReadFloat reads 32 bits")
    ok &= sub("DataSync.cpp",
              b"    ulong result = reinterpret_cast<ulong&>(theValue);\n    //result = LONG_NATIVE_TO_LITTLEE(result);\n    WriteBytes(&result, sizeof(result));",
              b"    uint32_t result = reinterpret_cast<uint32_t&>(theValue);  // 32 bits on disk\n    //result = LONG_NATIVE_TO_LITTLEE(result);\n    WriteBytes(&result, sizeof(result));",
              "DataSync.cpp: WriteFloat writes 32 bits")

    ok &= sub("DataSync.cpp",
              b"#include <vector>\n",
              b"#include <vector>\n#include <cstdint>\n",
              "DataSync.cpp: <cstdint> for uint32_t")

    print("== a corrupt or foreign save must not be fatal ==")
    # Nothing in the game catches DataReaderException, so a save the serialiser
    # cannot parse -- one written by a previous build with a different layout,
    # a truncated file, a PC save from a different version -- aborts the
    # process from inside Board::LoadGame. The caller already copes with a
    # false return: LoadBoardGame does CreateBoard(); if (!LoadGame())
    # RemoveBoard(); so the half-populated board is discarded and the game
    # starts fresh, and the next exit overwrites the bad file with a good one.
    #
    # Guarded at the reader entry points, not deeper: LoadGame, and the two
    # profile readers on the same serialiser.
    ok &= sub("Board.cpp",
              b"\tBuffer aBuf;\n\tif (mApp->ReadBufferFromFile(theSavePath, &aBuf, false))\n\t{\n\t\tDataReader aDR;",
              b"\tBuffer aBuf;\n\ttry {\n\tif (mApp->ReadBufferFromFile(theSavePath, &aBuf, false))\n\t{\n\t\tDataReader aDR;",
              "Board.cpp: LoadGame guards the read")
    ok &= sub("Board.cpp",
              b"\t\t\treturn true;\n\t\t}\n\t}\n\treturn false;\n}",
              b"\t\t\treturn true;\n\t\t}\n\t}\n\t} catch (DataReaderException&) {\n\t\t/* unreadable save: start fresh, see apply-cxx-modern.py */\n\t}\n\treturn false;\n}",
              "Board.cpp: LoadGame catches DataReaderException")
    # The user list and per-user profile, same serialiser, read at startup and
    # on profile select -- a stale users.dat would crash before the menu.
    ok &= sub("ProfileMgr.cpp",
              b"        DataSync aDS(aDR);\n        SyncUsersDat(aDS);\n    }\n}",
              b"        DataSync aDS(aDR);\n        try { SyncUsersDat(aDS); }\n        catch (DataReaderException&) { if (mProfilesMap) mProfilesMap->clear(); }  /* unreadable: no users */\n    }\n}",
              "ProfileMgr.cpp: ReadUsersDat catches DataReaderException")
    ok &= sub("ProfileMgr.cpp",
              b"        SyncData(aDS);\n        SetCheatFlag(6, false);\n    }\n}",
              b"        try { SyncData(aDS); }\n        catch (DataReaderException&) { /* unreadable profile: defaults */ }\n        SetCheatFlag(6, false);\n    }\n}",
              "ProfileMgr.cpp: LoadFromMemory catches DataReaderException")
    # highscores.dat, read at startup; a bad one falls back to the defaults the
    # missing-file path already uses.
    ok &= sub("HighScoreMgr.cpp",
              b"        DataSync aDS(aDR);\n        SyncData(&aDS);\n    }\n    else\n        MakeDefaultHighScores();",
              b"        DataSync aDS(aDR);\n        try { SyncData(&aDS); }\n        catch (DataReaderException&) { ClearAllScoreLists(); MakeDefaultHighScores(); }\n    }\n    else\n        MakeDefaultHighScores();",
              "HighScoreMgr.cpp: loader catches DataReaderException")

    print("== 64-bit array counting ==")
    ok &= regex_all(r"sizeof\(mCheatCodes\)\s*/\s*4",
                    "sizeof(mCheatCodes) / sizeof(mCheatCodes[0])",
                    "mCheatCodes element count",
                    files=["Board.cpp"])

    # --- 6. delete[] through void* ------------------------------------------
    # Undefined behaviour: the compiler cannot know the element type. It
    # happened to work with a byte-sized allocation and no destructors.
    print("== delete[] through void* ==")
    ok &= regex_all(r"delete\[\]\s+mMemoryHandle;",
                    "delete[] (char*)mMemoryHandle;",
                    "cast before delete[]",
                    files=["DataSync.cpp"])

    print()
    if ok:
        print("== all C++/64-bit fixups applied ==")
    else:
        print("== SOME FIXUPS FAILED -- see [FAILED] above ==")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
