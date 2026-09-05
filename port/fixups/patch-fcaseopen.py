#!/usr/bin/env python3
"""
patch-fcaseopen.py -- device-qualify every opendir and fopen in fcaseopen.c.

Why this is a script and not another sed_required entry
-------------------------------------------------------
The edit inserts a block *after* an anchor it does not consume
(`#include <dirent.h>`), so the anchor survives the edit. A tree carrying an
older version of the block therefore still matches the anchor, and applying
again stacks a second copy:

    error: redefinition of 'nx_qualified_opendir'

sed_required's "already applied" test compares against the *current* text, so it
cannot recognise an *older* version of the same block. This script keys on a
stable marker instead, removes whatever block is there, and writes the current
one -- so it converges from a pristine tree, from any previous version, and from
itself.

What the block does
-------------------
fcaseopen is the case-insensitive fallback taken when fopen fails, and it walks
paths with opendir/readdir. Every one of those calls, and fopen itself, receives
a relative path -- "data/Liddie12.jpg", ".", "./data". A relative path makes
newlib resolve the device implicitly, that resolution yields a null devoptab
here, and the caller then reads a function pointer straight out of it:

    opendir -> devoptab_t::diropen_r   at offset 0x78
    fopen   -> devoptab_t::open_r      at offset 0x10

which are exactly the two fault addresses seen on hardware. Naming the device
explicitly removes the implicit lookup, and a missing file returns NULL as the
surrounding code already expects. Missing files are normal here:
ImageLib::GetImage probes .png, .jpg, .gif and .tga in turn, so most of these
calls are *meant* to fail.
"""
import io
import re
import sys

MARKER = "nx_qualified_opendir"
# Anchored AFTER every include, not in the middle of them.
#
# The block ends with `#define fopen ...`, and a macro named fopen must not be
# in force while system headers are still being parsed -- a later header
# re-declaring fopen would have its declaration rewritten to the static wrapper
# and clash with it. `#endif` closes the platform include block, so nothing
# further is included after this point.
ANCHOR = "#include <errno.h>\n#endif"

BLOCK = r'''
#ifdef __SWITCH__
/* --- device qualification (port/fixups/patch-fcaseopen.py) ---------------
 * Relative paths make newlib resolve the device implicitly; that lookup can
 * yield a null devoptab, and opendir/fopen then read a function pointer out of
 * it (diropen_r at 0x78, open_r at 0x10 -- both seen as fault addresses on
 * hardware). getcwd() reports the sdmc:-qualified directory, so joining onto
 * it removes the implicit lookup entirely.
 *
 * Each wrapper is defined before its #define, so the body still reaches the
 * real function. */
#include <unistd.h>
#include <stdio.h>

static void nx_qualify(const char *thePath, char *theOut, size_t theOutSize)
{
    char aCwd[512];
    char *aColon;

    theOut[0] = '\0';
    if (getcwd(aCwd, sizeof aCwd) == NULL)
        return;

    /* Only rewrite the path if the working directory actually names a device.
     *
     * The point of this wrapper is to replace an implicit device lookup with an
     * explicit one. If getcwd() itself comes back without a device -- say
     * "/switch/insaniquarium" rather than "sdmc:/switch/insaniquarium" -- then
     * prepending it produces an ABSOLUTE path with no device, which is strictly
     * worse than the relative path we were given: a relative path at least
     * resolves against the current directory. Leaving theOut empty makes the
     * callers below fall through to the original path untouched. */
    aColon = strchr(aCwd, ':');
    if (aColon == NULL)
        return;

    if (thePath[0] == '/')
    {
        /* Truncate the cwd to just the device ("sdmc:") and keep the path's
           own leading slash. Dropping it -- "sdmc:" + "switch/x" -- yields
           "sdmc:switch/x", which is a different, relative location. */
        aColon[1] = '\0';
        snprintf(theOut, theOutSize, "%s%s", aCwd, thePath);
    }
    else
    {
        /* Trim a trailing slash before joining. getcwd() may or may not return
           one depending on what was passed to chdir() -- this port chdir's to
           "sdmc:/switch/<dir>/" -- and "sdmc:/switch/x//data/y.gif" is not a
           path the fs layer is obliged to accept. */
        size_t aLen = strlen(aCwd);
        while (aLen > 0 && aCwd[aLen - 1] == '/')
            aCwd[--aLen] = '\0';
        snprintf(theOut, theOutSize, "%s/%s", aCwd, thePath);
    }
}

static DIR *nx_qualified_opendir(const char *thePath)
{
    char aBuf[768];
    if (thePath == NULL || thePath[0] == '\0' || strchr(thePath, ':') != NULL)
        return opendir(thePath);
    nx_qualify(thePath, aBuf, sizeof aBuf);
    /* Empty means "not qualifiable" -- use the path as given rather than
       failing, so this can only ever add resolution, never remove it. */
    return opendir(aBuf[0] ? aBuf : thePath);
}

static FILE *nx_qualified_fopen(const char *thePath, const char *theMode)
{
    char aBuf[768];
    if (thePath == NULL || thePath[0] == '\0' || strchr(thePath, ':') != NULL)
        return fopen(thePath, theMode);
    nx_qualify(thePath, aBuf, sizeof aBuf);
    return fopen(aBuf[0] ? aBuf : thePath, theMode);
}

#define opendir nx_qualified_opendir
#define fopen   nx_qualified_fopen
#endif /* __SWITCH__ */
'''


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    path = sys.argv[1]
    s = io.open(path, encoding="utf-8", errors="ignore").read()

    had = MARKER in s
    if had:
        # Remove whatever version is present, by its guard, so this converges
        # from any earlier shape of the block rather than stacking on it.
        # ALL of them, not one. A tree can already carry two copies -- that is
        # the "redefinition of nx_qualified_opendir" this script exists to
        # repair -- and removing a single block would leave one behind and add
        # a fresh one, which is no repair at all.
        pat = re.compile(r"\n#ifdef __SWITCH__\n(?:(?!#endif).)*?" + MARKER +
                         r".*?\n#endif[^\n]*\n", re.S)
        s, n = pat.subn("\n", s)
        if n == 0:
            print("   [FAILED] fcaseopen: a previous block is present but could "
                  "not be identified for replacement")
            return 1

    if ANCHOR not in s:
        print(f"   [FAILED] fcaseopen: anchor '{ANCHOR}' not found")
        return 1

    s = s.replace(ANCHOR, ANCHOR + "\n" + BLOCK.strip() + "\n", 1)

    # Disable the case-insensitive fallback on Switch.
    #
    # fcaseopen tries fopen and, on failure, runs casepath: an opendir/readdir
    # scan of the directory. ImageLib probes .png/.jpg/.gif/.tga for the base
    # image and then again for the alpha companion, so an alpha-only font runs
    # SEVEN directory scans through newlib before the file that exists is
    # tried -- and newlib's fd/dir tables are exactly what the BTD5 port
    # documents as corrupting under this kind of churn. The fonts that work are
    # the ones with a direct image: they hit on the first probe and never scan.
    #
    # The descriptor data/Liddie12.txt opens fine through this same function
    # moments earlier; only the image, reached after the scans, fails. That is
    # the one difference between the working and failing paths.
    #
    # Files on the card are case-correct (verified), so the fallback buys
    # nothing here; tools/verify-assets.py --fix-case covers the case-mismatch
    # scenario offline instead.
    OLD_FALLBACK = (
        "    FILE *f = fopen(path, mode);\n"
        "#if !defined(_WIN32)\n"
        "    if (!f)\n")
    NEW_FALLBACK = (
        "    FILE *f = fopen(path, mode);\n"
        "#if !defined(_WIN32) && !defined(__SWITCH__)\n"
        "    if (!f)\n")
    if NEW_FALLBACK in s:
        pass
    elif OLD_FALLBACK in s:
        s = s.replace(OLD_FALLBACK, NEW_FALLBACK, 1)
    else:
        print("   [FAILED] fcaseopen: casepath fallback not found to disable")
        return 1

    # Same for fcaseopenat, used when a resource base folder is set. Not the
    # path this port takes (ChangeDirHook returns true, so the base is empty),
    # but the same scan and the same hazard.
    OLD_AT = (
        "    FILE *f = fopen(full, mode);\n"
        "    if (!f)\n")
    NEW_AT = (
        "    FILE *f = fopen(full, mode);\n"
        "#ifndef __SWITCH__\n"
        "    if (!f)\n")
    if NEW_AT not in s:
        if OLD_AT not in s:
            print("   [FAILED] fcaseopen: fcaseopenat fallback not found to disable")
            return 1
        s = s.replace(OLD_AT, NEW_AT, 1)
        # close the #ifndef after the fallback block's closing brace
        s = s.replace(
            "            f = fopen(r, mode);\n        }\n    }\n    return f;\n#else\n",
            "            f = fopen(r, mode);\n        }\n    }\n#endif\n    return f;\n#else\n", 1)

    io.open(path, "w", encoding="utf-8", newline="").write(s)

    print("   [%s] fcaseopen: device-qualify opendir and fopen"
          % ("replaced" if had else "ok"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
