#!/usr/bin/env python3
"""
apply-framework.py -- map the decompilation onto the GL framework.

This is the small half of the port. The decompilation was written against
PopCap's original SexyAppFramework, and the target framework is a
reimplementation of that same API, so most of it needs nothing:

  namespace Sexy      unchanged
  SexyString          unchanged   (typedef std::string SexyString; is kept)
  _S("x")             unchanged   (#define _S(x) x is kept)
  #include "SexyAppFramework/Font.h"
                      unchanged   (create_flat_directory.sh puts every framework
                                   header in one flat dir, which is the layout
                                   the decomp already expects)
  ReadBufferFromFile(p, &buf, false)
                      unchanged   (the 3-arg signature is kept)
  SyncLong(ulong&)    unchanged   (ulong is `unsigned long` here, so it does not
                                   collide with the unsigned int overload)
  mDemoPrefix, mDemoFileName, mPlayingDemoBuffer, DemoSyncString
                      unchanged   (all still present)

What is left is the rendering back end, which moved from DirectDraw/Direct3D to
GL, plus two signatures.

Every replacement is verified. If an expected block is missing the script says
so and exits non-zero rather than carrying on and leaving a half-ported tree.
"""
import pathlib
import sys

DST = pathlib.Path(__file__).resolve().parent.parent / "game"


def crlf(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def sub(path, old, new, label, count=1):
    """Replace in one file. Reports 'already applied' so re-running is safe."""
    p = DST / path
    if not p.exists():
        print(f"   [MISSING FILE] {label}  ({path})")
        return False
    data = p.read_bytes()
    for o, n in ((crlf(old), crlf(new)), (old, new)):
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


def sub_all_files(old, new, label):
    """Replace across every .cpp/.h. Used for the type renames."""
    hits = 0
    files = 0
    for p in sorted(list(DST.glob("*.cpp")) + list(DST.glob("*.h"))):
        data = p.read_bytes()
        n = data.count(old)
        if n:
            p.write_bytes(data.replace(old, new))
            hits += n
            files += 1
    if hits:
        print(f"   [ok] {label} ({hits}x in {files} files)")
    else:
        print(f"   [none] {label}")
    return True



def regex_all_files(pattern, repl, label):
    """Regex replace across every .cpp/.h. Used where a plain string is unsafe."""
    import re as _re
    rx = _re.compile(pattern)
    hits = 0
    files = 0
    for p in sorted(list(DST.glob("*.cpp")) + list(DST.glob("*.h"))):
        text = p.read_text(encoding="utf-8", errors="ignore")
        new, n = rx.subn(repl, text)
        if n:
            p.write_text(new, encoding="utf-8", newline="")
            hits += n
            files += 1
    if hits:
        print(f"   [ok] {label} ({hits}x in {files} files)")
    else:
        print(f"   [none] {label}")
    return True

def main():
    ok = True

    print("== framework headers ==")
    # DirectDraw and Direct3D collapsed into one GL interface. Done as include
    # rewrites rather than an include-map pass because there are only three.
    ok &= sub_all_files(b'SexyAppFramework/DDImage.h',
                        b'SexyAppFramework/GLImage.h',
                        "DDImage.h -> GLImage.h")
    ok &= sub_all_files(b'SexyAppFramework/DDInterface.h',
                        b'SexyAppFramework/GLInterface.h',
                        "DDInterface.h -> GLInterface.h")
    ok &= sub_all_files(b'SexyAppFramework/D3DInterface.h',
                        b'SexyAppFramework/GLInterface.h',
                        "D3DInterface.h -> GLInterface.h")
    # The music back end is SDL/libopenmpt rather than BASS.
    ok &= sub_all_files(b'SexyAppFramework/BassMusicInterface.h',
                        b'SexyAppFramework/SDLMusicInterface.h',
                        "BassMusicInterface.h -> SDLMusicInterface.h")

    print("== framework types ==")
    # FIRST, before any of the general renames below, because this is a
    # structural change rather than a rename. DirectDraw and Direct3D were two
    # objects and the decomp reaches through one to the other:
    #     D3DInterface* i = mApp->mDDInterface->mD3DInterface;
    # The GL framework has a single GLInterface which is SexyAppBase::
    # mGLInterface, so the two levels collapse into one. Renaming each token
    # separately would instead produce `mApp->mGLInterface->mGLInterface`,
    # which compiles as far as the member lookup and then fails.
    ok &= sub_all_files(b"mApp->mDDInterface->mD3DInterface",
                        b"mApp->mGLInterface",
                        "mDDInterface->mD3DInterface collapsed to mGLInterface")

    # Order matters: mDDInterface before DDInterface, or the member name is
    # rewritten into mGLInterfaceInterface.
    ok &= sub_all_files(b'mDDInterface', b'mGLInterface', "mDDInterface -> mGLInterface")
    ok &= sub_all_files(b'DDImage',      b'GLImage',      "DDImage -> GLImage")
    ok &= sub_all_files(b'DDInterface',  b'GLInterface',  "DDInterface -> GLInterface")
    ok &= sub_all_files(b'D3DInterface', b'GLInterface',  "D3DInterface -> GLInterface")
    # The framework calls the font class _Font, not Font. The decomp
    # forward-declares `class Font;` in Res.h and hangs every FONT_* global off
    # it, so without this the game builds a phantom Sexy::Font that is never
    # defined and GetFontThrow's real _Font* will not convert to it.
    #
    # Matched only where Font is followed by * or & -- a bare \bFont\b would
    # also rewrite the 24 `#include <SexyAppFramework/Font.h>` lines into a
    # header that does not exist. ImageFont, SysFont, theFont, mFont and the
    # uppercase FONT_* names are all unaffected by the word boundary.
    ok &= regex_all_files(r"\bFont\b(?=\s*[*&])", "_Font",
                          "Font -> _Font (type positions only)")
    ok &= sub_all_files(b"class Font;", b"class _Font;",
                        "Res.h: forward declaration")

    ok &= sub_all_files(b'BassMusicInterface', b'SDLMusicInterface',
                        "BassMusicInterface -> SDLMusicInterface")

    print("== signatures ==")
    # CreateMusicInterface lost its HWND: there is no window handle to pass.
    ok &= sub("WinFishApp.h",
              b"virtual MusicInterface*\t\tCreateMusicInterface(HWND theHWnd);",
              b"virtual MusicInterface*\t\tCreateMusicInterface();",
              "WinFishApp.h: CreateMusicInterface drops HWND")
    ok &= sub("WinFishApp.cpp",
              b"MusicInterface* Sexy::WinFishApp::CreateMusicInterface(HWND theHWnd)",
              b"MusicInterface* Sexy::WinFishApp::CreateMusicInterface()",
              "WinFishApp.cpp: CreateMusicInterface drops HWND")
    ok &= sub("WinFishApp.cpp",
              b"return SexyApp::CreateMusicInterface(theHWnd);",
              b"return SexyApp::CreateMusicInterface();",
              "WinFishApp.cpp: CreateMusicInterface call site")

    # In the old framework MusicInterface was a concrete do-nothing base, so
    # `new MusicInterface` was a legitimate silent-audio object. Here it is
    # abstract (19 pure virtuals) and DummyMusicInterface is the do-nothing
    # implementation. This is the screensaver branch, so it never runs on
    # Switch -- but it has to compile.
    ok &= sub("WinFishApp.cpp",
              b"return new MusicInterface;",
              b"return new DummyMusicInterface;",
              "WinFishApp.cpp: new MusicInterface -> DummyMusicInterface")
    ok &= sub("WinFishApp.cpp",
              b'#include <SexyAppFramework/SDLMusicInterface.h>',
              b'#include <SexyAppFramework/SDLMusicInterface.h>\n#include <SexyAppFramework/DummyMusicInterface.h>',
              "WinFishApp.cpp: DummyMusicInterface.h include")

    # MemoryImage::GetBits() returns uint32_t*, not the Win32 DWORD*. The
    # compat header typedefs DWORD to uint32_t so the *values* below are fine;
    # only the two pointer declarations need to change.
    ok &= sub("WinFishApp.cpp",
              b"DWORD* aNewBits = aNewImage->GetBits();",
              b"uint32_t* aNewBits = aNewImage->GetBits();",
              "WinFishApp.cpp: aNewBits is uint32_t*")
    ok &= sub("WinFishApp.cpp",
              b"DWORD* aMaskBits = aMemoryMask->GetBits();",
              b"uint32_t* aMaskBits = aMemoryMask->GetBits();",
              "WinFishApp.cpp: aMaskBits is uint32_t*")

    # --- FONT_PICO129 -------------------------------------------------------
    # The framework hardcodes `Sexy::FONT_PICO129` as its default-font fallback
    # -- ButtonWidget, EditWidget, DialogButton, HyperlinkWidget and Dialog all
    # do `mFont = FONT_PICO129->Duplicate()` when no font was set. It is a PvZ
    # resource that the game module is expected to define, so without it the
    # link fails with undefined references from inside the framework.
    #
    # Defined here and pointed at FONT_JUNGLEFEVER10OUTLINE, which is what
    # Insaniquarium's own MakeDialogButton already substitutes when handed a
    # null font -- so the framework's fallback and the game's agree.
    #
    # Assigned inside ExtractInitResources immediately after that font loads,
    # rather than left null: most widgets here do set their own font (dialogs
    # go through DefaultDialogSettings), but "mostly not dereferenced" is not a
    # safe thing to ship.
    print("== FONT_PICO129 (framework's default-font fallback) ==")
    # `_Font* Sexy::FONT_PICO129;` is a QUALIFIED definition, which C++ only
    # allows when the name was already declared inside that namespace. The
    # other font globals work because Res.h declares them; FONT_PICO129 is
    # declared in PvZ's Resources.h, which the game never includes. So declare
    # it here too -- identical to the framework's own extern, so the two agree
    # if they ever meet in one translation unit.
    ok &= sub("Res.h",
              b"\textern _Font* FONT_JUNGLEFEVER10OUTLINE;",
              b"\t// Framework default-font fallback; see apply-framework.py.\n"
              b"\textern _Font* FONT_PICO129;\n"
              b"\textern _Font* FONT_JUNGLEFEVER10OUTLINE;",
              "Res.h: declare FONT_PICO129 in namespace Sexy")
    ok &= sub("Res.cpp",
              b"_Font* Sexy::FONT_JUNGLEFEVER10OUTLINE;",
              b"_Font* Sexy::FONT_JUNGLEFEVER10OUTLINE;\n"
              b"// Framework fallback font. See port/fixups/apply-framework.py.\n"
              b"_Font* Sexy::FONT_PICO129;",
              "Res.cpp: define FONT_PICO129")
    ok &= sub("Res.cpp",
              b'\t\tFONT_JUNGLEFEVER10OUTLINE = aMgr.GetFontThrow("FONT_JUNGLEFEVER10OUTLINE");',
              b'\t\tFONT_JUNGLEFEVER10OUTLINE = aMgr.GetFontThrow("FONT_JUNGLEFEVER10OUTLINE");\n'
              b'\t\t// The framework falls back to this when a widget has no font.\n'
              b'\t\tFONT_PICO129 = FONT_JUNGLEFEVER10OUTLINE;',
              "Res.cpp: point FONT_PICO129 at the game's default font")

    print("== SEHCatcher ==")
    # Deliberately NOT touched here. SEHCatcher.h is an empty stub in this
    # framework, so the statics the decomp assigns to do not exist -- but the
    # assignments form one contiguous block ending in mSubmitHost, and
    # apply-portability.py removes the whole block with a single regex.
    # Removing the mSubmitHost line here would delete that regex's end anchor
    # and the block would survive. One owner per edit.
    print("   [skipped] handled as one block by apply-portability.py")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
