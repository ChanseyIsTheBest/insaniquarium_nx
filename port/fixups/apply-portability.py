#!/usr/bin/env python3
"""
apply-portability.py -- remove Windows-only code from the decompilation.

Kept separate from apply-framework.py on purpose, following the split the
upstream Insaniquarium port uses: that file maps one framework onto another,
this one removes things that have no meaning on a console. Mixing them makes it
impossible to tell later which change was which.

Where a shim can absorb the difference it lives in compat/winfish_compat.h
instead, and the source is left alone -- GetTickCount, FindFirstFileA,
MessageBox, OutputDebugString and GetUserNameA are all handled that way. What
is left here is code with no console equivalent at all:

  * the Windows screensaver installer (Insaniquarium shipped as a .scr)
  * HKEY_CURRENT_USER\\Control Panel\\Desktop reads and writes
  * PopCap's crash reporter, which posted to popcap.com
  * BetaSupport, which drew a GDI dialog for PopCap's internal beta testers

Everything is verified. A block that does not apply is reported and the script
exits non-zero, because a half-applied fixup produces code that compiles and
misbehaves rather than code that fails to build.
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


def wrap_win32(path, signature, fallback, label):
    """
    Wrap a whole function body in #ifdef _WIN32, with a fallback for everyone
    else. The closing brace is found at column 0 rather than by counting lines,
    so the match survives edits made earlier in the file.
    """
    p = DST / path
    if not p.exists():
        print(f"   [MISSING FILE] {label}  ({path})")
        return False

    data = p.read_bytes()
    sig = crlf(signature)

    head = data.find(sig)
    if head < 0:
        sig = signature
        head = data.find(sig)
        if head < 0:
            if b"#ifdef _WIN32" in data and label.encode() not in data:
                # can't tell definitively; report and let the caller decide
                pass
            print(f"   [FAILED] {label}  ({path}: signature not found)")
            return False

    # already wrapped?
    if data.count(b"#ifdef _WIN32", head, head + 200) > 0:
        print(f"   [already applied] {label}")
        return True

    # body runs from the signature to the first '}' sitting at column 0
    end = data.find(b"\n}", head)
    if end < 0:
        print(f"   [FAILED] {label}  ({path}: no closing brace)")
        return False
    end += len(b"\n}")

    body = data[head:end]
    nl = b"\r\n" if b"\r\n" in body else b"\n"

    new = (b"#ifdef _WIN32" + nl + body + nl +
           b"#else" + nl + crlf(fallback) + nl +
           b"#endif")

    p.write_bytes(data[:head] + new + data[end:])
    print(f"   [ok] {label}")
    return True


def main():
    ok = True

    # --- 1. entry point ----------------------------------------------------
    # Window.cpp is replaced wholesale by port.sh (step 4) rather than patched:
    # the original is a WinMain plus a dead WndProc, and it also has to gain the
    # data-directory lookup. Nothing to do here.
    print("== entry point ==")
    print("   [handled by port.sh] Window.cpp replaced from fixups/")

    # --- 1b. data directory ------------------------------------------------
    # The framework resolves resources by PREFIXING them (GetResourcePath), but
    # the decompilation opens "fishsongs/", "properties/" and "userdata/" as
    # bare relative paths, which never get prefixed. ChangeDirHook is the
    # framework's own escape hatch: return true and it skips prefixing
    # altogether, trusting the working directory instead. So chdir to the data
    # folder and every relative path in the game works untouched.
    #
    # The Win32 body is screensaver-only (it returns false unless running as a
    # .scr) and uses SetCurrentDirectoryA, so it is kept but guarded.
    print("== data directory ==")
    ok &= sub("WinFishApp.cpp",
              b"""bool Sexy::WinFishApp::ChangeDirHook(const char* theIntendedPath)
{
	if (!IsScreenSaver())
		return false;

	SexyString aRegPath = mScreenSaverRegKey + "Directory";
	SexyString aDirectoryPath;

	if (RegistryReadString(aRegPath, &aDirectoryPath))
		if (SetCurrentDirectoryA(aDirectoryPath.c_str()) != 0)
			return true;

	return false;
}""",
              b"""bool Sexy::WinFishApp::ChangeDirHook(const char* theIntendedPath)
{
#ifdef __SWITCH__
	// Returning true tells the framework the working directory is already
	// correct, so it skips GetResourcePath() prefixing entirely and the game's
	// own relative paths resolve as they did on Windows. The directory was
	// worked out in main() and can be any folder under switch/.
	(void)theIntendedPath;

	if (!NxChangeToDataDir())
		return false;

	// Saves have to be pointed somewhere explicitly, and this is the only
	// place that knows where. None of SexyAppBase::Init()'s platform branches
	// call SetAppDataFolder() on Switch, so it would otherwise fall through to
	//     SetAppDataFolder(GetResourcePath("savedata"))
	// and, because returning true above means SetResourceFolder() never ran,
	// GetResourcePath("savedata") is just "savedata" -- with NO trailing
	// slash. The game builds its paths by concatenation:
	//     GetAppDataFolder() + "userdata/highscores.dat"
	// which would silently become "savedatauserdata/highscores.dat". Nothing
	// crashes; the game simply never remembers anything. NxGetDataDir() ends
	// in a slash, so setting it here produces correct absolute paths and the
	// framework's fallback is skipped (it only fires when this is empty).
	SetAppDataFolder(NxGetDataDir());
	return true;
#elif defined(_WIN32)
	if (!IsScreenSaver())
		return false;

	SexyString aRegPath = mScreenSaverRegKey + "Directory";
	SexyString aDirectoryPath;

	if (RegistryReadString(aRegPath, &aDirectoryPath))
		if (SetCurrentDirectoryA(aDirectoryPath.c_str()) != 0)
			return true;

	return false;
#else
	(void)theIntendedPath;
	return false;
#endif
}""",
              "WinFishApp.cpp: ChangeDirHook sets working dir AND app data folder")

    ok &= sub("WinFishApp.cpp",
              b'#include "WorkerThread.h"',
              b'#include "WorkerThread.h"\n#ifdef __SWITCH__\n#include "nx_datadir.h"\n#endif',
              "WinFishApp.cpp: nx_datadir.h include")

    # --- 2. crash reporter -------------------------------------------------
    # SEHCatcher.h is an empty stub in this framework, so these statics do not
    # exist. They addressed users at feedback@popcap.com and posted reports to
    # www.popcap.com; neither has existed for years.
    print("== crash reporter ==")
    p = DST / "SexyApp.cpp"
    if p.exists():
        data = p.read_bytes()
        pat = re.compile(
            rb"\tSEHCatcher::mCrashMessage\s*=.*?"
            rb"SEHCatcher::mSubmitHost\s*=\s*\"www\.popcap\.com\";\r?\n",
            re.S)
        new, n = pat.subn(b"", data, count=1)
        if n:
            p.write_bytes(new)
            print("   [ok] SexyApp.cpp: SEHCatcher message block removed")
        elif b"SEHCatcher::mCrashMessage" not in data:
            print("   [already applied] SexyApp.cpp: SEHCatcher message block")
        else:
            print("   [FAILED] SexyApp.cpp: SEHCatcher message block")
            ok = False
    else:
        print("   [MISSING FILE] SexyApp.cpp")
        ok = False

    # mShowUI is the entire body of an if. Deleting just that line would leave
    # the if dangling over whatever follows -- it compiles, and the next
    # statement silently becomes conditional. The whole if goes.
    ok &= sub("WinFishApp.cpp",
              b"\t\tSEHCatcher::mShowUI = false;",
              b"\t\t;",
              "WinFishApp.cpp: SEHCatcher::mShowUI -> no-op statement")

    # --- 3. BetaSupport ----------------------------------------------------
    # PopCap's internal beta-tester validation: a GDI dialog plus an HTTP call.
    # mBetaValidate is initialised to false and never set anywhere, so
    # Validate() was already unreachable in the retail build. BetaSupport.cpp
    # is excluded from the build in CMakeLists.txt; these remove the references.
    print("== BetaSupport (dead code, GDI + HTTP) ==")
    ok &= sub("SexyApp.cpp",
              b'#include "BetaSupport.h"',
              b'',
              "SexyApp.cpp: BetaSupport.h include removed")
    ok &= sub("SexyApp.cpp",
              b"\tmBetaSupport = new BetaSupport((WinFishApp*)this);",
              b"\tmBetaSupport = nullptr;",
              "SexyApp.cpp: BetaSupport never constructed")
    ok &= sub("SexyApp.cpp",
              b"    if (mBetaSupport)\n        delete mBetaSupport;",
              b"    // BetaSupport is never constructed on this platform.",
              "SexyApp.cpp: BetaSupport delete removed")

    # mBetaValidate lives on the ORIGINAL SexyAppBase (line 156 of the
    # framework the decomp shipped with), not on this one. It is the flag that
    # gated the beta check, and it was only ever set to false -- so the guarded
    # call was already unreachable in the retail build. Both references go.
    ok &= sub("SexyApp.cpp",
              b"\tmBetaValidate = false;",
              b"\t// mBetaValidate: framework member that does not exist here, and was"
              b"\n\t// only ever false. See PreDisplayHook below.",
              "SexyApp.cpp: mBetaValidate assignment removed")
    ok &= sub("SexyApp.cpp",
              b"\tif (mBetaValidate && !mBetaSupport->Validate())",
              b"\tif (false)   // was: mBetaValidate && !mBetaSupport->Validate()",
              "SexyApp.cpp: beta validation call removed")

    # mChangeDirTo was a std::string on the ORIGINAL SexyAppBase (line 133).
    # This framework has mResourceDir instead, with different semantics, so
    # rather than aliasing one to the other the game keeps its own stub. Every
    # read of it sits inside `if (CheckForVista())`, which the compat header
    # now pins to false, so those branches are dead -- but dead code still has
    # to compile.
    ok &= sub("SexyApp.h",
              b"        BetaSupport*                mBetaSupport;",
              b"        BetaSupport*                mBetaSupport;\n"
              b"        // Was a SexyAppBase member in the framework the decomp shipped\n"
              b"        // with. Only read inside if (CheckForVista()), which is false\n"
              b"        // here, so it stays empty and nothing observes it.\n"
              b"        std::string                 mChangeDirTo;",
              "SexyApp.h: mChangeDirTo stub (was a framework member)")

    # --- 4. screensaver ----------------------------------------------------
    # Insaniquarium also shipped as a Windows screensaver (.scr). All of this
    # installs and configures it through the registry and SystemParametersInfo.
    print("== screensaver installer ==")
    ok &= wrap_win32("WinFishApp.cpp",
                     b"void Sexy::WinFishApp::SetScreenSaver(const char* thePath)",
                     b"void Sexy::WinFishApp::SetScreenSaver(const char*) {}",
                     "SetScreenSaver -> no-op")
    ok &= wrap_win32("WinFishApp.cpp",
                     b"void Sexy::WinFishApp::GetSystemScreenSaverPath(SexyString& theDest)",
                     b"void Sexy::WinFishApp::GetSystemScreenSaverPath(SexyString& theDest) { theDest = \"\"; }",
                     "GetSystemScreenSaverPath -> empty")
    ok &= wrap_win32("WinFishApp.cpp",
                     b"void Sexy::WinFishApp::ApplyScreenSaverSettings()",
                     b"void Sexy::WinFishApp::ApplyScreenSaverSettings() {}",
                     "ApplyScreenSaverSettings -> no-op")
    ok &= wrap_win32("WinFishApp.cpp",
                     b"bool GetFileLastWriteTime(const char* path, FILETIME* outTime)",
                     b"bool GetFileLastWriteTime(const char*, void*) { return false; }",
                     "GetFileLastWriteTime -> false")
    ok &= wrap_win32("WinFishApp.cpp",
                     b"bool Sexy::WinFishApp::DoScrCopy(SexyString& theScrSvrPath)",
                     b"bool Sexy::WinFishApp::DoScrCopy(SexyString&) { return false; }",
                     "DoScrCopy -> false")

    # --- 5. HTML template --------------------------------------------------
    # Writes an HTML file to temp/ and opens it in a browser, for the
    # registration and update pages. There is no browser here, and mkdir()
    # below it is the MSVC single-argument form from <direct.h>.
    print("== HTML template / browser ==")
    ok &= sub("SexyApp.cpp",
              b"#include <direct.h>",
              b"#ifdef _WIN32\n#include <direct.h>\n#endif",
              "SexyApp.cpp: direct.h guarded")
    ok &= sub("SexyApp.cpp",
              b"\tmkdir(\"temp\");",
              b"#ifdef _WIN32\n\tmkdir(\"temp\");\n#else\n\tmkdir(\"temp\", 0755);\n#endif",
              "SexyApp.cpp: mkdir takes a mode")

    # --- 6. path separators ------------------------------------------------
    # Most of the decomp's backslashes are inside the screensaver and registry
    # code that has just been #ifdef'd out. These two are live paths that the
    # game opens on every run, and nothing here treats '\' as a separator.
    print("== path separators ==")
    ok &= sub("ProfileMgr.cpp",
              b'"userdata\\\\%s%d.dat"',
              b'"userdata/%s%d.dat"',
              "ProfileMgr.cpp: save path separator")
    ok &= sub("SexyApp.cpp",
              b'LoadProperties("properties\\\\partner.xml", false, checkSig);',
              b'LoadProperties("properties/partner.xml", false, checkSig);',
              "SexyApp.cpp: partner.xml path separator")
    # The fishsongs pattern goes through the compat FindFirstFileA, which
    # normalises separators itself, but the path built for each song does not.
    ok &= sub("WinFishApp.cpp",
              b'SexyString aFilePath = "fishsongs\\\\";',
              b'SexyString aFilePath = "fishsongs/";',
              "WinFishApp.cpp: fishsongs path separator")

    # The one that actually stops the game starting. The framework's own call
    # site uses a forward slash; this one, in the game, does not, and the
    # failure reads "Resource file not found: properties\resources.xml" -- with
    # the file sitting right there under properties/.
    ok &= sub("WinFishApp.cpp",
              b'ParseResourcesFile("properties\\\\resources.xml")',
              b'ParseResourcesFile("properties/resources.xml")',
              "WinFishApp.cpp: resources.xml path separator")

    # Goes through the compat FindFirstFileA, which normalises separators
    # itself, so this already worked -- but a path that only works because a
    # shim rewrites it is a trap for whoever reads it next.
    ok &= sub("WinFishApp.cpp",
              b'FindFirstFileA("fishsongs\\\\*.txt"',
              b'FindFirstFileA("fishsongs/*.txt"',
              "WinFishApp.cpp: fishsongs glob separator")

    # The HTML template scratch directory. Dead on Switch -- it exists to write
    # a page and open it in a browser -- but the separators should still be
    # right if anything ever does reach it.
    ok &= sub("SexyApp.cpp",
              b'FindFirstFile("temp\\\\tpl*.html"',
              b'FindFirstFile("temp/tpl*.html"',
              "SexyApp.cpp: temp glob separator")
    ok &= sub("SexyApp.cpp",
              b'std::string("temp\\\\")',
              b'std::string("temp/")',
              "SexyApp.cpp: temp path separator")
    ok &= sub("SexyApp.cpp",
              b'StrFormat("temp\\\\tpl%04d.html"',
              b'StrFormat("temp/tpl%04d.html"',
              "SexyApp.cpp: temp output separator")

    # NOT converted, deliberately:
    #
    #   mRegKey = "PopCap\\Insaniquarium"
    #   mScreenSaverRegKey = "ScreenSaver\\"
    #
    # These are REGISTRY key names, not filesystem paths -- backslash is the
    # correct separator for them, RegEmu stores them verbatim, and rewriting
    # them would rename every key and orphan the settings already saved under
    # the old names.
    #
    # Also left alone: the "\\" path joins at WinFishApp.cpp ~985/988 and the
    # screensaver ones, all of which sit behind `if (!CheckForVista()) return;`
    # or inside #ifdef _WIN32, and so never execute here.

    print()
    if ok:
        print("== all portability fixups applied ==")
    else:
        print("== SOME FIXUPS FAILED -- see [FAILED] above ==")
        print("   The decomp has probably moved upstream. Fix the pattern here")
        print("   rather than editing port/game/, which is regenerated.")
    # --- 7. WorkerThread lifetime ------------------------------------------
    # The original news a WorkerThread and never deletes it. On Windows that
    # was invisible: the process exited and the OS reaped the thread. Here the
    # applet has to return to hbmenu cleanly, and a live thread at exit can
    # hang or crash that. The replacement WorkerThread has a destructor that
    # signals and joins -- this makes it actually reachable.
    print("== WorkerThread lifetime ==")
    ok &= sub("WinFishApp.cpp",
              b"""Sexy::WinFishApp::~WinFishApp()
{
	if (mBoard)
		SaveCurrentUserData();""",
              b"""Sexy::WinFishApp::~WinFishApp()
{
	// Joins the loading thread. The original leaked it; that is fine when the
	// process is about to die, and not fine when an applet has to hand control
	// back. Done first so nothing below races a task still in flight.
	if (mWorkerThread)
	{
		delete mWorkerThread;
		mWorkerThread = NULL;
	}

	if (mBoard)
		SaveCurrentUserData();""",
              "WinFishApp.cpp: WorkerThread deleted (and joined) on shutdown")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
