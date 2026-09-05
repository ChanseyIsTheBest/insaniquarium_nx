#!/usr/bin/env python3
"""
patch-diagnostics.py -- insert the two framework diagnostics, replacing any
earlier version of themselves.

Why a script and not two more sed_required entries
--------------------------------------------------
sed_required matches a *starting state*. That works once, from a pristine
checkout, and then stops working: once an edit has been applied, its pattern
describes text that no longer exists, so a tree carrying version 1 of a block
cannot be migrated to version 2. The failure is
`[FAILED] ... pattern not found`, and it has now happened four times on this
port -- the GLESv2 link line, the fcaseopen block, and both of these.

These two are especially prone to it because they get revised: the popup logger
went printf -> NxLogRaw, and the image logger went ungated -> gated -> NxLogRaw.

Each patch below rewrites the *region between two stable landmarks*, so the
current text of the region is irrelevant. Pristine, version 1, version 2 and
version 3 all converge on the same result.

Both diagnostics deliberately use NxLogRaw rather than printf. Common.h does
`#define printf(...) Sexy::PrintF(__VA_ARGS__)`, which formats into a
std::string and writes through stdio -- and both of these run on the loading
thread while the main thread is also writing. That combination crashed inside
_write_r at devoptab offset 0x20.
"""
import io
import re
import sys

POPUP_BLOCK = '''
	/* Log and flush on the way IN, before the branch.
	 *
	 * Popup has two paths and the earlier patch only covered the deferred one.
	 * The primary-thread path printf()s -- which now reaches NxLogRaw -- and then
	 * calls errorApplicationShow, which blocks until the user dismisses it; the
	 * process then exits and the buffered line goes with it. That is why a run
	 * whose font clearly failed produced a log with no error in it at all.
	 *
	 * Doing it here covers both paths and cannot be bypassed by whichever branch
	 * is taken. */
	{
		NxLogRaw("FATAL ERROR (popup)\\n===\\n%s\\n", theString.c_str());
		NxLogFlush();
	}

#ifdef __SWITCH__
	/* Return instead of showing anything.
	 *
	 * Both of Popup's paths block forever here. The primary one calls
	 * errorApplicationShow; the deferred one queues a DeferredMessageBox and
	 * spins on SDL_Delay until the main thread drains it -- which never happens
	 * when the main thread is itself waiting on the loading thread that raised
	 * the popup. Disassembly showed the log above executing and then the process
	 * hanging in Error, so the message was written but the run never ended
	 * cleanly enough for anyone to read it.
	 *
	 * The caller already treats Popup as advisory: FontData::Error returns false
	 * afterwards and the loader carries on. Returning here turns a hang into a
	 * logged failure. */
	return;
#endif

'''

EGLTEARDOWN_BLOCK = """
#ifdef __SWITCH__
    /* Graceful shutdown for the EGL backend.
     *
     * On this backend mWindow is an EGLDisplay, not an SDL_Window, and SDL video
     * was never initialised -- so the SDL_DestroyWindow the other platforms use
     * dereferences an EGLDisplay as an SDL_Window struct. That was the crash on
     * the game's Exit button. Release the GL context, surface and display in the
     * order EGL requires, then let SDL_Quit stop the audio thread; it is the
     * audio thread still running during process teardown that produced every
     * "crash on exit" report. */
    if (mWindow)
    {
        eglMakeCurrent((EGLDisplay)mWindow, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (mContext) eglDestroyContext((EGLDisplay)mWindow, (EGLContext)mContext);
        if (mSurface) eglDestroySurface((EGLDisplay)mWindow, (EGLSurface)mSurface);
        eglTerminate((EGLDisplay)mWindow);
    }
    mWindow = nullptr; mContext = nullptr; mSurface = nullptr;
#else
    SDL_DestroyWindow((SDL_Window*)mWindow); mWindow = nullptr;
#endif
    SDL_Quit();
"""

CURSORIMG_BLOCK = """
		mCursorImages[theCursorNum] = theImage;
#ifdef __SWITCH__
		/* Hand the game's own cursor artwork to the virtual pointer.
		 *
		 * EnforceCursor() below only knows SDL_SetCursor, which does nothing on a
		 * backend that never initialised SDL video. The overlay in nx_pointer does
		 * the drawing here, so give it the pixels: GetBits() is the framework's
		 * ARGB buffer and is rebuilt on demand if the image was palletized.
		 *
		 * Hotspot at the centre: these cursors are authored that way (the pointer
		 * arrow's tip sits at (27,27) in a 54x54 image), and EnforceCursor uses
		 * width/2, height/2 for the same reason. */
		if (MemoryImage *aMem = dynamic_cast<MemoryImage *>(theImage))
		{
			if (uint32_t *aBits = aMem->GetBits())
				nxp_set_cursor_slot(theCursorNum, aBits, aMem->GetWidth(), aMem->GetHeight(),
				                    aMem->GetWidth() / 2, aMem->GetHeight() / 2);
		}
#endif
		EnforceCursor();
"""

CURSORSEL_BLOCK = """
	mCursorNum = theCursorNum;
#ifdef __SWITCH__
	nxp_select_cursor(theCursorNum);   /* pointer / hand / dragging / text */
#endif
	EnforceCursor();
"""

LOADTHREAD_BLOCK = """
		/* Keep the thread object and join it at shutdown instead of detaching.
		 *
		 * std::thread::detach() THROWS here. The main thread's stack in every
		 * crash report of this port reads
		 *     StartLoadingThread -> std::thread::detach -> __throw_system_error
		 *     -> terminate -> abort -> exit -> __libnx_exit
		 * exit() then tears down the filesystem devoptab while the loading thread
		 * is still running, and that thread faults on the now-null table at
		 * whatever it touches next -- stat_r at 0x40, open_r at 0x10, diropen_r
		 * at 0x78. Every "font failed to load" was this: the file was there and
		 * the filesystem had been pulled out from under the loader.
		 *
		 * join works on this platform -- WorkerThread uses it and runs fine --
		 * so the thread is stored and joined in the destructor. It must be
		 * joined or std::thread's destructor calls terminate. */
		gLoadingThread = std::thread(LoadingThreadProcStub, this);
"""

LOADTHREAD_DECL_BLOCK = """
#include <thread>
#ifdef __SWITCH__
#include "nx_pointer.h"   /* game cursors -> virtual pointer, see SetCursorImage */
#endif

/* The loading thread. Stored rather than detached; see StartLoadingThread. */
static std::thread gLoadingThread;

"""

LOADTHREAD_JOIN_BLOCK = """
	/* The loading thread was stored, not detached (see StartLoadingThread), so
	 * it has to be joined before this object goes away: a joinable std::thread
	 * being destroyed calls std::terminate. By this point Shutdown() has run
	 * and mLoadingThreadCompleted is set, so the join returns immediately. */
	if (gLoadingThread.joinable())
		gLoadingThread.join();

"""

TRYLOAD_BLOCK = """

	/* Report every candidate and whether its file was even present.
	 *
	 * "Failed to Load Image" says only that the whole resolution failed. This
	 * says which of the four extensions were tried, and separates "the file is
	 * not there" from "the file is there and the loader rejected it" -- which is
	 * the one distinction static analysis could not settle: the descriptor
	 * data/Liddie12.txt parses fine from the same directory, and every failing
	 * GIF decodes cleanly in a reference decoder. */
	for (const auto& [aKnownExt, aLoader] : kImageExts)
	{
		if (!theExt.empty() && !EqualsIgnoreCase(theExt, aKnownExt))
			continue;
		const std::string aTry = theBaseName + std::string(aKnownExt);
		const bool aPresent = Sexy::FileExists(aTry);
		NxLogRaw("  tried %-40s file %s\\n",
		         aTry.c_str(), aPresent ? "EXISTS -> loader refused it"
		                                : "absent");
	}
	NxLogFlush();

"""

SHAREDIMG_BLOCK = """

		{
			/* Do not cache a failed load.
			 *
			 * try_emplace INSERTS the key before the image is loaded, and the
			 * loader only runs when it inserts. So a load that returns null
			 * leaves a permanent null entry, and every later request for that
			 * name returns it without going near ImageLib::GetImage -- no retry,
			 * and no diagnostic either, because the code that would report the
			 * failure is never reached.
			 *
			 * That is how "Failed to Load Image" was reported by a font while
			 * ImageLib's own failure log stayed silent: disassembly puts that log
			 * on exactly the both-null path and correctly gated, so it would have
			 * fired had the loader run at all.
			 *
			 * Erasing the entry restores the retry and lets the failure be seen.
			 * theVariant is part of the key, so the erase must use both halves. */
			if (aSharedImageRef.mSharedImage->mImage == nullptr)
			{
				NxLogRaw("image: not caching failed load of '%s'\\n",
				         theFileName.c_str());
				NxLogFlush();
				mSharedImageMap.erase(
					SharedImageMap::key_type(anUpperFileName, anUpperVariant));
				return SharedImageRef();
			}
		}

"""

FONTIMG_BLOCK = """

				{
					/* Log at the failure site, where nothing can bypass it.
					 *
					 * The diagnostic in ImageLib::GetImage never fired for this,
					 * because GetSharedImage only calls GetImage when try_emplace
					 * actually inserts -- a second request for a name that already
					 * failed returns the cached null image without going near the
					 * loader. Here there is no cache in the way, and aFileName is
					 * the resolved path the font actually asked for. */
					NxLogRaw("font: %s could not load image '%s'\\n",
					         mSourceFile.c_str(), aFileName.c_str());
					NxLogFlush();
				}
					Error("Failed to Load Image");

"""

SDLBOX_BLOCK = """

	/* SDL_ShowSimpleMessageBox is skipped on Switch.
	 *
	 * This backend never calls SDL_Init(SDL_INIT_VIDEO) -- the window is an
	 * EGLDisplay created directly -- so this call cannot show anything, and it
	 * runs BEFORE errorApplicationShow. A run whose font failed produced no error
	 * box and no log line at all, which is what a hang here looks like from the
	 * outside. errorApplicationShow below is the one that actually works. */
#ifndef __SWITCH__
	SDL_ShowSimpleMessageBox(SDL_MESSAGEBOX_ERROR, "FATAL ERROR", theString.c_str(), (SDL_Window*)mWindow);
#endif

"""

PRINTF_BLOCK = """

#ifdef __SWITCH__
	/* Route the framework's own logging through NxLogRaw.
	 *
	 * This line used to be std::fwrite(..., stdout), which is the exact call
	 * that faulted in _write_r: the loading thread opens and closes asset files
	 * continuously (ImageLib probes four extensions per image), that mutates
	 * newlib's shared fd tables, and a concurrent stdio write reads a corrupted
	 * fd->device slot. Every printf in the framework reaches here, because
	 * Common.h does #define printf(...) Sexy::PrintF(__VA_ARGS__).
	 *
	 * It also restores the log's contents: with stdout no longer freopen'd to
	 * the log file, RegEmu lines, resource errors and the rest would otherwise
	 * only reach svcOutputDebugString and never the card. */
	NxLogRaw("%s", buffer.c_str());
#else
	std::fwrite(buffer.data(), 1, buffer.size(), stdout);
#endif
"""

IMAGE_BLOCK = '''
	/* Gated on lookForAlphaImage so only TOP-LEVEL failures are reported: the
	 * alpha probe calls GetImage(alphaPath, false) recursively, and most images
	 * have no "_" companion, so an ungated version logged two misses for every
	 * image that had loaded perfectly well. NxLogRaw, not printf: see the file
	 * header of port/fixups/patch-diagnostics.py.
	 *
	 * Braces around the whole body, deliberately. Without them the `if` guards
	 * only the first statement and the NxLogFlush() below runs for EVERY image
	 * -- a filesystem resize plus an SD sync per image, which is the exact cost
	 * the buffering exists to avoid. */
	if (anImage == NULL && lookForAlphaImage)
	{
		/* Name the exact candidates. "Failed to Load Image" from a font
		 * descriptor says nothing about which of the twelve possible paths was
		 * tried, and the alpha spelling differs per folder in this game:
		 * data/ uses _Liddie12.gif, images/ uses loaderbar_.gif. */
		const auto aSlashEnd =
			(aLastSlashPos != std::string::npos) ? aLastSlashPos + 1 : 0;
		NxLogRaw("image: could not resolve '%s'\\n"
		         "       tried '%s' + .png/.jpg/.gif/.tga\\n"
		         "       tried '%s' + those (leading-underscore alpha)\\n"
		         "       tried '%s' + those (trailing-underscore alpha)\\n",
		         theFilename.c_str(), aFilename.c_str(),
		         (theFilename.substr(0, aSlashEnd) + "_" +
		          theFilename.substr(aSlashEnd)).c_str(),
		         (theFilename + "_").c_str());
		/* Flush: a failed image is usually the last thing before a font error
		 * and then shutdown, and buffered text does not survive that. */
		NxLogFlush();
	}

'''



DECL = ('/* NxLogRaw is defined in platform/switch/nx_logc.c, compiled as C.\n'
        ' * Declared here at namespace scope: a linkage specification is only\n'
        ' * legal there, and putting `extern "C"` inside the function body gives\n'
        ' * "expected unqualified-id before string constant". */\n'
        'extern "C" int NxLogRaw(const char *, ...);\n'
        'extern "C" void NxLogFlush(void);\n\n')


# Matches ANY version of the declaration block, including an older one that
# declared NxLogRaw but not NxLogFlush.
DECL_RE = re.compile(
    r'/\* NxLogRaw is defined.*?\*/\n'
    r'extern "C" int NxLogRaw\(const char \*, \.\.\.\);\n'
    r'(?:extern "C" void NxLogFlush\(void\);\n)?'
    r'\n?', re.S)


def ensure_decl(path, before_re, label):
    """Put the extern "C" declarations at namespace scope, above the function.

    Removes any existing block first rather than skipping when one is found.
    Testing merely for the presence of `extern "C" int NxLogRaw` was wrong: a
    tree carrying the earlier block -- which declared NxLogRaw but not
    NxLogFlush, added later -- looked already-declared and never gained the
    second line, giving `'NxLogFlush' was not declared in this scope`. Same
    mistake as matching a starting state anywhere else in this port.
    """
    s = io.open(path, encoding="utf-8", errors="ignore").read()

    s, removed = DECL_RE.subn("", s)
    if s.count('extern "C" int NxLogRaw') or s.count('extern "C" void NxLogFlush'):
        print(f"   [FAILED] {label}: a declaration is present in an unrecognised form")
        return 1

    m = re.search(before_re, s)
    if not m:
        print(f"   [FAILED] {label}: could not place the NxLogRaw declaration")
        return 1

    s = s[:m.start()] + DECL + s[m.start():]
    io.open(path, "w", encoding="utf-8", newline="").write(s)
    return 0



# Blocks this script used to insert somewhere it no longer inserts them.
#
# Moving a block to a new location does NOT remove the old copy -- patch_region
# only rewrites the region it now targets -- so the previous site has to be
# cleaned up by name or the tree ends up carrying both.
#
# Done by scanning lines rather than with a regex: the first attempt used one
# and was written by a script that doubled its backslashes, so it silently
# matched nothing and left the stale block in place.
STALE_MARKERS = [
    "FATAL ERROR (deferred from a worker thread)",
]


def remove_stale(path):
    lines = io.open(path, encoding="utf-8", errors="ignore").read().split("\n")
    out = []
    i = 0
    removed = False
    while i < len(lines):
        if any(m in lines[i] for m in STALE_MARKERS):
            removed = True
            # the statement, plus any continuation lines up to its ");"
            while i < len(lines) and not lines[i].rstrip().endswith(");"):
                i += 1
            i += 1
            # an immediately following NxLogFlush(); and one blank line
            if i < len(lines) and lines[i].strip() == "NxLogFlush();":
                i += 1
            if i < len(lines) and lines[i].strip() == "":
                i += 1
            continue
        out.append(lines[i])
        i += 1
    if removed:
        io.open(path, "w", encoding="utf-8", newline="").write("\n".join(out))


def patch_region(path, head_re, tail_re, block, label):
    """Replace whatever lies between two landmarks with `block`."""
    s = io.open(path, encoding="utf-8", errors="ignore").read()

    pat = re.compile("(" + head_re + ")(.*?)(" + tail_re + ")", re.S)
    m = pat.search(s)
    if not m:
        print(f"   [FAILED] {label}: landmarks not found in {path}")
        return 1

    had = m.group(2).strip() != ""
    if m.group(2) == block:
        print(f"   [already applied] {label}")
        return 0

    s = s[:m.start()] + m.group(1) + block + m.group(3) + s[m.end():]
    io.open(path, "w", encoding="utf-8", newline="").write(s)
    print(f"   [{'replaced' if had else 'ok'}] {label}")
    return 0



def patch_region_literal(path, head, tail, block, label):
    """patch_region with landmarks given as plain text.

    Regex landmarks have to escape whatever the C source contains, and C source
    is full of backslashes -- `printf("FATAL ERROR\\n===\\n%s\\n", ...)` needs four
    levels of escaping by the time a script is writing a script that writes a
    regex. Two attempts at that got it wrong and matched nothing. Passing the
    text literally and escaping it here removes the problem.
    """
    return patch_region(path, re.escape(head), re.escape(tail), block, label)


def main():
    if len(sys.argv) != 5:
        print(__doc__)
        return 2

    sexyappbase, imagelib, common, imagefont = (
        sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])

    # --- graceful exit: proper EGL teardown instead of SDL_DestroyWindow(EGLDisplay) ---
    rc = patch_region_literal(
        sexyappbase,
        head='\tWriteDemoBuffer();\n    \n',
        tail='}\n\nvoid SexyAppBase::ClearUpdateBacklog',
        block=EGLTEARDOWN_BLOCK,
        label="destructor tears down EGL, not an imaginary SDL window")
    if rc:
        return rc

    # --- game cursors drive the virtual pointer ---
    rc = patch_region_literal(
        sexyappbase,
        head='void SexyAppBase::SetCursorImage(int theCursorNum, Image* theImage)\n{\n\tif ((theCursorNum >= 0) && (theCursorNum < NUM_CURSORS))\n\t{\n',
        tail='\t}\n}\n',
        block=CURSORIMG_BLOCK,
        label="game cursor images feed the virtual pointer")
    if rc:
        return rc
    rc = patch_region_literal(
        sexyappbase,
        head='void SexyAppBase::SetCursor(int theCursorNum)\n{\n',
        tail='}\n\nint SexyAppBase::GetCursor()',
        block=CURSORSEL_BLOCK,
        label="cursor selection drives the virtual pointer")
    if rc:
        return rc

    # --- the actual bug: detach() throws on Switch ---
    rc = patch_region_literal(
        sexyappbase,
        head='\t\t//_beginthread(LoadingThreadProcStub, 0, this);\n',
        tail='#endif\n\t}\n}\nvoid SexyAppBase::CursorThreadProc()',
        block=LOADTHREAD_BLOCK,
        label="store the loading thread instead of detaching it")
    if rc:
        return rc
    rc = patch_region_literal(
        sexyappbase,
        # Tail must be the SPECIFIC next include. A bare "#include " matched
        # this block's own "#include <thread>" on re-run, making the region
        # empty and inserting a second static -- a redefinition error.
        head='#include <mutex>\n',
        tail='#include "misc/Debug.h"',
        block=LOADTHREAD_DECL_BLOCK,
        label="declare the loading thread object")
    if rc:
        return rc
    rc = patch_region_literal(
        sexyappbase,
        head='SexyAppBase::~SexyAppBase()\n{\n\tShutdown();\n',
        tail='\tDialogMap::iterator aDialogItr = mDialogMap.begin();',
        block=LOADTHREAD_JOIN_BLOCK,
        label="join the loading thread at destruction")
    if rc:
        return rc

    rc = patch_region_literal(
        imagelib,
        head=('static Image* TryLoadByExt(const std::string& theBaseName, '
              'std::string_view theExt)\n{\n\tfor (const auto& [aKnownExt, aLoader] '
              ': kImageExts)\n\t{\n\t\tif (theExt.empty() || '
              'EqualsIgnoreCase(theExt, aKnownExt))\n\t\t{\n\t\t\tif (Image* aImage = '
              'aLoader(theBaseName + std::string(aKnownExt)))\n\t\t\t\treturn aImage;'
              '\n\t\t}\n\t}\n'),
        tail='\treturn nullptr;\n}',
        block=TRYLOAD_BLOCK,
        label="report every image candidate tried")
    if rc:
        return rc

    rc = patch_region_literal(
        sexyappbase,
        head=('\t\t\taSharedImageRef.mSharedImage->mImage = '
              'GetImage(theFileName,false);\n\t}\n'),
        tail='\treturn aSharedImageRef;',
        block=SHAREDIMG_BLOCK,
        label="do not cache failed image loads")
    if rc:
        return rc

    rc = patch_region_literal(
        imagefont,
        head='\t\t\t\telse\n\t\t\t\t{\n',
        tail='\t\t\t\t\treturn false;\n',
        block=FONTIMG_BLOCK,
        label="name the font image that failed to load")
    if rc:
        return rc

    rc = patch_region_literal(
        sexyappbase,
        head='            printf("FATAL ERROR\\n===\\n%s\\n", theString.c_str());\n',
        tail='#ifdef __SWITCH__\n        ErrorApplicationConfig c;',
        block=SDLBOX_BLOCK,
        label="skip SDL_ShowSimpleMessageBox on Switch")
    if rc:
        return rc

    rc = patch_region(
        common,
        head_re=(r'__android_log_write\(ANDROID_LOG_INFO, "PvZPortable", '
                 r'buffer\.c_str\(\)\);\n#endif\n'),
        tail_re=r'\n\}\n\nint Sexy::Rand\(\)',
        block=PRINTF_BLOCK,
        label="route Sexy::PrintF through NxLogRaw")
    if rc:
        return rc

    remove_stale(sexyappbase)

    rc = patch_region(
        sexyappbase,
        # Popup()'s own errorApplicationCreate names theString, which is what
        # distinguishes it from MsgBox() -- that has the identical
        # "else { // add new instance" shape but a parameter called theText.
        # The function entry: unique, and stable regardless of what the body
        # is restructured into later.
        head_re=r'void SexyAppBase::Popup\(const std::string& theString\)\n\{\n',
        tail_re=r'    if \(gSexyAppBase == NULL',
        block=POPUP_BLOCK,
        label="log popups (both branches)")
    if rc:
        return rc

    rc = patch_region(
        imagelib,
        head_re=(r'ApplyAlphaAsImage\(anImage, static_cast<uint32_t>\(gAlphaComposeColor\)\);'
                 r'\n\t\t\}\n\t\}\n'),
        tail_re=r'\treturn anImage;\n\}',
        block=IMAGE_BLOCK,
        label="log unresolved images")
    if rc:
        return rc

    # Declarations LAST, deliberately.
    #
    # Running them first looked natural and was wrong: an older block carried
    # its own (illegal, block-scope) `extern "C" int NxLogRaw`, so ensure_decl
    # saw the string, assumed a declaration existed and skipped -- and
    # patch_region then deleted that block, leaving the calls with no
    # declaration at all. Rewriting the regions first means whatever is left is
    # this script's own output, so the check is asking about the right thing.
    rc = ensure_decl(sexyappbase, r"void SexyAppBase::Popup\(",
                     "log popups (both branches)")
    if rc:
        return rc

    # Above TryLoadByExt, not ImageLib::GetImage: TryLoadByExt is defined
    # earlier in the file and now logs too, and a declaration placed after its
    # definition is no use to it ("'NxLogRaw' was not declared in this scope").
    rc = ensure_decl(imagelib, r"static Image\* TryLoadByExt\(",
                     "log unresolved images")
    if rc:
        return rc

    rc = ensure_decl(common, r"void Sexy::PrintF\(",
                     "route Sexy::PrintF through NxLogRaw")
    if rc:
        return rc

    return ensure_decl(imagefont, r"bool FontData::HandleCommand\(",
                       "name the font image that failed to load")


if __name__ == "__main__":
    sys.exit(main())
