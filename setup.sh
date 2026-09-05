#!/usr/bin/env bash
# setup.sh -- fetch the two upstream projects and graft this port into them.
#
# Nothing upstream is vendored into this repository. The decompilation and the
# framework are both cloned here, which keeps the package small, lets upstream
# fixes flow in with a git pull, and avoids redistributing either.
#
# The build deliberately reuses the framework's OWN CMakeLists rather than a
# replacement written here. That file already wires up SDL-Mixer-X, libopenmpt,
# the SDL2 static-target patching and the NRO packaging, and it is known to
# work because it is what the Zuma build uses. All this script changes is which
# game module it compiles.
set -euo pipefail

# See build.sh: keeps the window open when double-clicked, so a failure is
# readable instead of a terminal that blinks and disappears.
on_exit() {
  local rc=$?
  if [ $rc -ne 0 ]; then
    echo
    echo "---------------------------------------------------------------"
    echo " setup.sh stopped (exit $rc). The reason is printed above."
    echo "---------------------------------------------------------------"
    if [ -t 0 ]; then
      echo
      read -r -p "Press Enter to close..." _
    fi
  fi
}
trap on_exit EXIT

for tool in git python3; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "$tool is not on PATH, and setup.sh needs it."
    echo "Use the same shell you built the Zuma NRO in."
    exit 1
  }
done

# A sed that does nothing is the failure mode that cost the most time on this
# port: three patterns silently missed because they were written against a
# different checkout, and nothing said so until the compiler did (or, worse,
# did not -- libpng simply went unlinked). This refuses to continue.
_has() {
  python3 -c 'import io,sys; sys.exit(0 if sys.argv[2] in io.open(sys.argv[1],encoding="utf-8",errors="ignore").read() else 1)' "$1" "$2"
}

sed_required() {
  local pattern="$1" replacement="$2" file="$3" label="$4"
  # Presence is decided in python, not by grep. `grep -F` with a multi-line
  # pattern matches each LINE independently, so a two-line pattern reports a hit
  # when only its first line is present -- which makes an already-applied edit
  # print [ok] as though it had just been made. The edit itself is still a
  # no-op, but a run that claims to have changed something it did not is exactly
  # the reporting failure this helper exists to prevent.
  # The ALREADY-APPLIED test runs FIRST, deliberately.
  #
  # Several of these edits insert a line above the text they match, so the
  # pattern is a substring of its own replacement and is still present
  # afterwards. Testing the pattern first would then re-apply the edit on every
  # run, stacking a duplicate line each time.
  if _has "$file" "$replacement"; then
    echo "   [already applied] $label"
    return 0
  fi

  if _has "$file" "$pattern"; then
    python3 - "$file" "$pattern" "$replacement" <<'PYSED'
import io, sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
s = io.open(path, encoding="utf-8", errors="ignore").read()
io.open(path, "w", encoding="utf-8", newline="").write(s.replace(old, new))
PYSED
    echo "   [ok] $label"
  elif false; then
    :
  else
    echo "   [FAILED] $label"
    echo "      pattern not found in $file:"
    echo "        $pattern"
    echo "      The framework has probably changed since this port was written."
    echo "      Fix the pattern in setup.sh rather than editing the tree by hand."
    exit 1
  fi
}

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT="$ROOT/external"

FRAMEWORK_REPO="${FRAMEWORK_REPO:-https://github.com/kyle-sylvestre/PvZ-Portable}"
FRAMEWORK_REF="${FRAMEWORK_REF:-zuma}"
WINFISH_REPO="${WINFISH_REPO:-https://github.com/vindirect/winfish}"

mkdir -p "$EXT"

# ---------------------------------------------------------------- sources ---

echo "== 1. decompilation =="
if [ -d "$EXT/winfish/.git" ]; then
  echo "   already present ($EXT/winfish)"
else
  git clone --depth 1 "$WINFISH_REPO" "$EXT/winfish"
fi

echo
echo "== 2. framework =="
if [ -d "$EXT/framework/.git" ]; then
  echo "   already present ($EXT/framework)"
else
  git clone --depth 1 --branch "$FRAMEWORK_REF" "$FRAMEWORK_REPO" "$EXT/framework"
fi

FW="$EXT/framework"
[ -f "$FW/CMakeLists.txt" ] || { echo "framework checkout looks wrong: no CMakeLists.txt"; exit 1; }

# ------------------------------------------------------------------ port ---

echo
echo "== 3. deriving the game sources =="
bash "$ROOT/port/port.sh"

echo
echo "== 4. grafting into the framework tree =="

# The game module. The framework's CMake globs one game directory; Zuma's is
# src/CircleShoot, ours is src/WinFish.
rm -rf "$FW/src/WinFish"
mkdir -p "$FW/src/WinFish"
cp "$ROOT/port/game"/*.cpp "$ROOT/port/game"/*.h "$FW/src/WinFish"/
# Excluded from the build (see below), so do not even copy them across.
rm -f "$FW/src/WinFish/BetaSupport.cpp" "$FW/src/WinFish/BetaSupport.h" \
      "$FW/src/WinFish/Beetlemuncher.cpp" "$FW/src/WinFish/ColorUtils.cpp"
echo "   src/WinFish: $(ls "$FW/src/WinFish" | wc -l) files"

# Compat shims, compiled as part of the game module.
cp "$ROOT/compat/winfish_compat.h" "$ROOT/compat/winfish_compat.cpp" "$FW/src/WinFish"/
echo "   compat shims copied"

# Switch platform layer: our input backend plus the pointer and data-dir code.
PLAT="$FW/src/SexyAppFramework/platform/switch"
cp "$ROOT/platform/Input.cpp"       "$PLAT/Input.cpp"
cp "$ROOT/platform/nx_pointer.c"    "$PLAT/nx_pointer.c"
cp "$ROOT/platform/nx_pointer.h"    "$PLAT/nx_pointer.h"
cp "$ROOT/platform/nx_datadir.cpp"  "$PLAT/nx_datadir.cpp"
cp "$ROOT/platform/nx_datadir.h"    "$PLAT/nx_datadir.h"
cp "$ROOT/platform/nx_log.cpp"      "$PLAT/nx_log.cpp"
cp "$ROOT/platform/nx_logc.c"       "$PLAT/nx_logc.c"
cp "$ROOT/platform/nx_log.h"        "$PLAT/nx_log.h"
cp "$ROOT/platform/nx_crash_handler.c" "$PLAT/nx_crash_handler.c"
echo "   platform/switch: Input.cpp, nx_pointer, nx_datadir, nx_log, nx_crash_handler"

# ----------------------------------------------------------- framework ------

echo
echo "== 5. framework edits =="

# a) The resource directory is hardcoded to PvZ's. We resolve it at runtime
#    instead (see platform/nx_datadir.cpp), and ChangeDirHook returns true so
#    this value is never actually used -- but leaving a wrong path in place
#    would be confusing to anyone reading it later.
if grep -q 'sdmc:/switch/PvZPortable/' "$FW/src/SexyAppFramework/SexyAppBase.cpp"; then
  sed -i 's|sdmc:/switch/PvZPortable/|sdmc:/switch/insaniquarium/|' \
      "$FW/src/SexyAppFramework/SexyAppBase.cpp"
  echo "   [ok] default mResourceDir (overridden at runtime anyway)"
else
  echo "   [already applied] default mResourceDir"
fi

# b) Draw the cursor immediately before the buffer swap. This is the only place
#    with the engine's GL context current and the frame finished.
GLI="$FW/src/SexyAppFramework/graphics/GLInterface.cpp"
if grep -q 'nxp_draw();' "$GLI"; then
  echo "   [already applied] nxp_draw hook"
else
  python3 - "$GLI" <<'PYEOF'
import sys, io
p = sys.argv[1]
s = io.open(p, encoding="utf-8", errors="ignore").read()

old = "#ifdef NINTENDO_SWITCH\n\teglSwapBuffers(mApp->mWindow, mApp->mSurface);"
new = ("#ifdef NINTENDO_SWITCH\n"
       "\tnxp_draw();   // on-screen cursor, on top of the finished frame\n"
       "\teglSwapBuffers(mApp->mWindow, mApp->mSurface);")
if old not in s:
    sys.exit("could not find the eglSwapBuffers call in GLInterface.cpp")
s = s.replace(old, new, 1)

# nx_pointer.h has its own extern "C" guard, sited below its libc includes.
# Wrapping the include again would drag <stdio.h> into extern "C".
inc = '#ifdef NINTENDO_SWITCH\n#include "nx_pointer.h"\n#endif\n'
if 'nx_pointer.h' not in s:
    idx = s.find("#include")
    s = s[:idx] + inc + s[idx:]

io.open(p, "w", encoding="utf-8", newline="").write(s)
print("   [ok] nxp_draw hook in GLInterface::Flush")
PYEOF
fi

# b2) Instrument the EGL setup. MakeWindow() returns silently from every EGL
#     failure path and never checks eglMakeCurrent, so "Failed to initialize
#     OpenGL 2.0" is the same message whichever step actually broke.
python3 "$ROOT/port/fixups/instrument-egl.py" "$PLAT/Window.cpp" || exit 1
python3 "$ROOT/port/fixups/instrument-egl.py" \
        "$FW/src/SexyAppFramework/graphics/GLInterface.cpp" || exit 1

# b3) Fix EGL's headers for the Switch build.
#
# GLPlatform.h includes <glad/gles2.h> at line 33, and glad embeds its own copy
# of khrplatform.h -- taking the __khrplatform_h_ include guard while omitting
# KHRONOS_APIENTRY, which glad does not need (it uses GLAD_API_PTR instead).
#
# When line 55 then includes <EGL/egl.h>, eglplatform.h asks for
# <KHR/khrplatform.h>, finds that guard already defined, and skips the real
# header. EGLAPIENTRY is defined as KHRONOS_APIENTRY, which is now an undefined
# identifier, so every EGL function typedef expands to
#     typedef EGLBoolean (KHRONOS_APIENTRY * PFNEGLCHOOSECONFIGPROC)(...)
# and fails with "expected ')' before '*' token" -- around 40 times, pointing at
# a system header that is perfectly correct.
#
# Defining it empty is exactly what khrplatform.h does on every non-Windows
# target. This only surfaced once NINTENDO_SWITCH was defined, because until
# then GLPlatform.h took the SDL branch and never included EGL at all -- which
# is a strong hint this path had not been compiled before.
sed_required "#include <switch.h>
#include <EGL/egl.h>" \
             "#include <switch.h>

/* glad (included above) embeds khrplatform.h and takes its include guard while
 * omitting KHRONOS_APIENTRY, so the real header is skipped below and
 * EGLAPIENTRY would expand to an undefined identifier. Empty is what
 * khrplatform.h defines it as on every non-Windows target. */
#ifndef KHRONOS_APIENTRY
#define KHRONOS_APIENTRY
#endif

#include <EGL/egl.h>" \
             "$FW/src/SexyAppFramework/graphics/GLPlatform.h" \
             "define KHRONOS_APIENTRY (glad steals khrplatform's include guard)"

# b4) Device-qualify fcaseopen's opendir and fopen.
#
# Done by a script rather than sed_required: the edit inserts a block after an
# anchor it does not consume, so a tree carrying an OLDER version of the block
# still matches the anchor and gets a second copy stacked on it
# ("error: redefinition of nx_qualified_opendir"). sed_required compares against
# the current text and cannot recognise an older version of the same block.
# patch-fcaseopen.py keys on a stable marker, removes whatever is there, and
# writes the current block -- converging from a pristine tree, from any previous
# version, and from itself.
python3 "$ROOT/port/fixups/patch-fcaseopen.py" \
        "$FW/src/SexyAppFramework/fcaseopen/fcaseopen.c" || exit 1

# b5) The two framework diagnostics.
#
# A script, not sed_required, for the same reason as fcaseopen: sed_required
# matches a STARTING state, so once an edit is applied its pattern no longer
# exists and a tree carrying version 1 cannot be migrated to version 2. These
# two get revised often -- the popup logger went printf -> NxLogRaw, the image
# logger went ungated -> gated -> NxLogRaw -- so they need to converge from any
# previous version, which patch-diagnostics.py does by rewriting the region
# between two stable landmarks.
python3 "$ROOT/port/fixups/patch-diagnostics.py" \
        "$FW/src/SexyAppFramework/SexyAppBase.cpp" \
        "$FW/src/SexyAppFramework/imagelib/ImageLib.cpp" \
        "$FW/src/SexyAppFramework/Common.cpp" \
        "$FW/src/SexyAppFramework/graphics/ImageFont.cpp" || exit 1

# c) Point the build at our game module and rename the target.
CM="$FW/CMakeLists.txt"

# NO coarse "already applied" guard around this block.
#
# There used to be one -- `if grep -q src/WinFish/\*.cpp` -- wrapping every edit
# below. It meant a tree set up once was treated as done forever, so nx_log.cpp
# and nx_crash_handler.c, added later, were never inserted into an existing
# checkout. The build then linked without them and died on undefined references
# to NxLogInit -- immediately after a setup run that reported success.
#
# One guard per edit, each checking for its own result. A new edit added later
# then reaches trees that already exist.
{
  # Rewrite the game module's path everywhere it appears rather than deleting
  # the lines that mention it. Deleting them leaves calls like
  #
  #     set_property(SOURCE
  #         APPEND_STRING PROPERTY COMPILE_FLAGS " -x objective-c++ ")
  #
  # which still parse, so nothing complains, and which are wrong. They sit
  # inside if(APPLE) and if(WIN32) so a Switch build never reaches them -- it
  # would be someone else, months later, who finds out.
  sed_required "src/CircleShoot/source/CircleShoot" "src/WinFish" "$CM" "game module path"

  # Each platform source is added independently and guarded on its own
  # presence, so a file appended to this list later still reaches a tree that
  # was set up before that file existed.
  add_platform_source() {
    # The presence check lives in python, not in a grep, because a plain
    # substring search is wrong here: nx_crash_handler.c also appears in the
    # set_source_files_properties block further down, so grep reports it as
    # present while it is missing from the SOURCES list -- and it then never
    # gets compiled. Matching the tab-indented source-list form specifically
    # distinguishes the two.
    python3 - "$CM" "$1" <<'PYADD'
import io, sys
path, name = sys.argv[1], sys.argv[2]
s = io.open(path, encoding="utf-8", errors="ignore").read()

PREFIX = "${CMAKE_CURRENT_SOURCE_DIR}/src/SexyAppFramework/platform/switch/"
entry = "\n\t\t" + PREFIX + name          # source-list form: tab indented
if entry in s:
    print("   [already present] " + name)
    sys.exit(0)

anchor = PREFIX + "Input.cpp"
if anchor not in s:
    print("   [FAILED] Input.cpp not found in the Switch source list")
    sys.exit(1)

io.open(path, "w", encoding="utf-8", newline="").write(
    s.replace(anchor, anchor + entry, 1))
print("   [ok] " + name + " added to the Switch sources")
PYADD
    if [ $? -ne 0 ]; then exit 1; fi
  }

  remove_platform_source() {
    python3 - "$CM" "$1" <<'PYDEL'
import io, sys
path, name = sys.argv[1], sys.argv[2]
s = io.open(path, encoding="utf-8", errors="ignore").read()
entry = "\n\t\t${CMAKE_CURRENT_SOURCE_DIR}/src/SexyAppFramework/platform/switch/" + name
if entry not in s:
    print("   [not built] " + name)
    sys.exit(0)
io.open(path, "w", encoding="utf-8", newline="").write(s.replace(entry, "", 1))
print("   [removed] " + name + " (set CRASH_HANDLER=1 to enable)")
PYDEL
  }

  add_platform_source nx_pointer.c
  add_platform_source nx_datadir.cpp
  add_platform_source nx_log.cpp
  add_platform_source nx_logc.c
  # The crash handler is OFF by default.
  #
  # It faults on entry and takes the real crash with it. Symbolising a report
  # against the ELF put the recursive backtrace at
  # `__libnx_exception_handler + 0x2c`, repeating until the stack overflowed:
  # something faults, libnx calls the handler, the handler faults, libnx calls
  # it again. The genuine fault is never reported and no [crash] dump is ever
  # written.
  #
  # The likely culprit is its first statement, `debug_log_flush()`, which
  # fsyncs -- an IPC call taking filesystem locks, from a thread that has just
  # faulted and may already hold them. Its own header warns about exactly this
  # class of problem for printf; fsync is no safer.
  #
  # Without it, Atmosphere reports the original fault, and the `anchor` line in
  # insaniquarium_nx.log makes that report symbolisable:
  #     base = anchor_address - (nm offset of NxLogInit)
  #
  # Re-enable with CRASH_HANDLER=1 once it has been made fault-safe.
  if [ "${CRASH_HANDLER:-0}" = "1" ]; then
    add_platform_source nx_crash_handler.c
  else
    remove_platform_source nx_crash_handler.c
  fi
  # libpng for nx_pointer's optional cursor.png. PNG::PNG is already linked
  # further up and carries its own include dirs; these are the plain names the
  # Switch link line uses for everything else.
  # Three independent edits to the Switch block, each phrased so it applies
  # whatever state the tree is already in.
  #
  # An earlier version described the PRISTINE link line and so could not upgrade
  # a tree that had already been through a previous package -- the pattern named
  # a line that no longer existed, and setup aborted. Each edit below matches
  # the smallest fragment that identifies its target, so it works on a fresh
  # clone and on a partly-migrated tree alike.

  # 1. libpng, for nx_pointer's optional cursor.png.
  sed_required "EGL glapi drm_nouveau)" \
               "EGL glapi drm_nouveau png z)" \
               "$CM" "link libpng for cursor.png"

  # 2. GLESv2. The framework resolves GL through glad at runtime and links no GL
  #    library at all, so `glCreateShader` in framework code is really
  #    `glad_glCreateShader`, a function pointer. nx_pointer.c is outside that
  #    arrangement -- it includes <GLES2/gl2.h> and calls GL directly -- so it
  #    needs the real library, and without it the link fails on 33 core GLES2
  #    symbols from that one file. GLESv2 goes first because it depends on
  #    glapi and static link order matters.
  #
  #    Matching "PRIVATE EGL glapi drm_nouveau" rather than the whole line means
  #    this applies whether or not "png z" is already there. The Android line
  #    ("PRIVATE GLESv2 EGL android log") cannot match it.
  sed_required "PRIVATE EGL glapi drm_nouveau" \
               "PRIVATE GLESv2 EGL glapi drm_nouveau" \
               "$CM" "link GLESv2 (nx_pointer calls GL directly)"

  # 3. Define NINTENDO_SWITCH -- see the note above the KHRONOS_APIENTRY fix for
  #    why the framework needs this. Anchored on the `elseif` rather than on the
  #    link line, so it is independent of edits 1 and 2 entirely.
  sed_required "elseif (NINTENDO_SWITCH)
" \
               "elseif (NINTENDO_SWITCH)
	target_compile_definitions(pvz-portable PRIVATE NINTENDO_SWITCH)
" \
               "$CM" "define NINTENDO_SWITCH (framework tests it, never defines it)"

  # Logging is off unless asked for.
  #
  # nx_logc.c compiles to no-ops without NX_DEBUG_LOG, which silences every
  # writer at once -- including the framework's whole printf stream, since
  # Common.h routes it to Sexy::PrintF and from there to NxLogRaw. A release
  # build creates no log file at all.
  #
  # This adds or removes its OWN line rather than editing the NINTENDO_SWITCH
  # one: that line is the anchor the define edit above keys on, and rewriting it
  # made that edit believe it had never run, so it re-inserted a duplicate.
  python3 - "$CM" "${DEBUG_LOG:-0}" <<'PYLOG'
import io, sys
path, want = sys.argv[1], sys.argv[2] == "1"
s = io.open(path, encoding="utf-8", errors="ignore").read()
LINE = "\ttarget_compile_definitions(pvz-portable PRIVATE NX_DEBUG_LOG)\n"
ANCHOR = "\ttarget_compile_definitions(pvz-portable PRIVATE NINTENDO_SWITCH)\n"
have = LINE in s
if want and not have:
    if ANCHOR not in s:
        print("   [FAILED] debug logging: NINTENDO_SWITCH anchor not found"); sys.exit(1)
    s = s.replace(ANCHOR, ANCHOR + LINE, 1)
    io.open(path, "w", encoding="utf-8", newline="").write(s)
    print("   [ok] debug logging ENABLED (DEBUG_LOG=1)")
elif have and not want:
    io.open(path, "w", encoding="utf-8", newline="").write(s.replace(LINE, "", 1))
    print("   [ok] debug logging off (set DEBUG_LOG=1 to enable)")
else:
    print("   [already applied] debug logging %s" % ("ENABLED" if want else "off"))
PYLOG
  [ $? -eq 0 ] || exit 1



  # --gc-sections is NOT applied, and must not be.
  #
  # It was added here to shrink the NRO while chasing a load failure, and it
  # broke OpenGL: the game reached GLInterface::Init and died on
  # GLAD_GL_ES_VERSION_2_0 == 0, i.e. EGL came up and the context was current,
  # but gladLoadGLES2(eglGetProcAddress) resolved no entry points.
  #
  # That is the classic --gc-sections failure. mesa's GL dispatch is reached by
  # NAME at runtime through eglGetProcAddress, so the linker sees no reference
  # to those stubs and collects them. Nothing is undefined at link time; the
  # symbols simply are not there when something asks for them by string.
  #
  # The working Zuma build does not use it either. If the NRO needs to shrink,
  # it has to come from linking less, not from collecting what looks unused.
  # naming
  # The NRO label shown in hbmenu. This is "PvZ Portable" even on the zuma
  # branch -- "Zuma Portable" appears only as the macOS bundle name, so
  # seding that one would rename the bundle and leave the NRO mislabelled.
  # NRO metadata: title, author, version, icon. nx_generate_nacp writes the
  # first three into the NACP hbmenu reads; nx_create_nro embeds the icon, which
  # must be a 256x256 JPEG (assets/icon.jpg is generated to that spec).
  #
  # The title accepts either the pristine name or the "Insaniquarium" earlier
  # packages set, so a tree from a previous package migrates rather than failing.
  if _has "$CM" 'NAME "PvZ Portable"'; then
    sed_required 'NAME "PvZ Portable"' 'NAME "Insaniquarium Deluxe"' "$CM" "NRO title"
  else
    sed_required 'NAME "Insaniquarium"' 'NAME "Insaniquarium Deluxe"' "$CM" "NRO title"
  fi
  sed_required 'AUTHOR "wszqkzqk, etc., a community-driven port"' \
               'AUTHOR "ChanseyIsTheBest"' "$CM" "NRO author"
  sed_required 'VERSION "0.1"' 'VERSION "1.0.0"' "$CM" "NRO version"
  cp "$ROOT/assets/icon.jpg" "$FW/icon-insaniquarium.jpg"
  sed_required 'ICON ${CMAKE_CURRENT_SOURCE_DIR}/icon-switch.jpg' \
               'ICON ${CMAKE_CURRENT_SOURCE_DIR}/icon-insaniquarium.jpg' \
               "$CM" "NRO icon"
  sed -i 's|MACOSX_BUNDLE_BUNDLE_NAME "Zuma Portable"|MACOSX_BUNDLE_BUNDLE_NAME "Insaniquarium"|' "$CM"
  sed_required "OUTPUT_NAME zuma-portable" "OUTPUT_NAME insaniquarium_nx" "$CM" "output filename"
  echo "   [ok] CMakeLists points at src/WinFish"
}

# d) The decomp is 2004 code: MSVC built it with /permissive, and clang/gcc are
#    stricter about the same constructs (ambiguous ButtonListener conversions
#    through the dialog hierarchy, temporaries bound to non-const Color&/Rect&).
if grep -q 'WINFISH_PERMISSIVE' "$CM"; then
  echo "   [already applied] permissive flags"
else
  cat >> "$CM" <<'CMEOF'

# --- WINFISH_PERMISSIVE ------------------------------------------------------
# Scoped to the game's own sources, NOT the whole target. Two reasons that
# matter rather than one that is tidy:
#
#   * -include winfish_compat.h is a C++ header (<cstdint>, <string>, inline
#     functions). Applied target-wide it is also fed to nx_pointer.c, which is
#     C, and that is a hard "fatal error: cstdint: No such file or directory".
#   * The header #defines MessageBox, FindFirstFile and DeleteFile. The
#     framework does not use those names today, but pushing macros into 200
#     framework files to serve 74 game files is a trap for whoever updates the
#     framework next.
#
# -fpermissive is here because the decompilation is 2004 code that MSVC built
# with /permissive. GCC rejects two things MSVC accepted: ambiguous conversions
# to ButtonListener (the class appears more than once in the dialog hierarchy)
# and temporaries bound to non-const Color&/Rect& references.
# The crash handler must not log through printf: newlib takes a per-stream lock
# and the faulting thread is usually the one already holding it, so the dump
# deadlocks and never reaches the card. NxLogRaw writes with write() instead.
set_source_files_properties(
    ${CMAKE_CURRENT_SOURCE_DIR}/src/SexyAppFramework/platform/switch/nx_crash_handler.c
    PROPERTIES COMPILE_DEFINITIONS "CRASH_LOG_PRINTF=NxLogRaw"
)

file(GLOB WINFISH_GAME_SOURCES ${CMAKE_CURRENT_SOURCE_DIR}/src/WinFish/*.cpp)
set_source_files_properties(${WINFISH_GAME_SOURCES} PROPERTIES
    COMPILE_OPTIONS
    "-fpermissive;-Wno-invalid-offsetof;-Wno-write-strings;-include;${CMAKE_CURRENT_SOURCE_DIR}/src/WinFish/winfish_compat.h"
)
CMEOF
  echo "   [ok] permissive flags + compat force-include (game sources only)"
fi

# e) The framework expects a flat directory of its own headers, which is the
#    layout the decompilation's #includes already assume.
echo
echo "== 6. flat framework directory =="
bash "$FW/scripts/create_flat_directory.sh" >/dev/null 2>&1 || true
echo "   $(ls "$FW/src/SexyAppFrameworkFlat/SexyAppFramework" 2>/dev/null | wc -l) headers linked"

# Verify what actually landed. sed_required aborts on a miss, but a partial run
# still leaves a tree that builds -- and the failure then surfaces as a compile
# error inside a system header, which points nowhere near setup.
echo
echo "== 7. verifying the framework edits =="
VERIFY_FAIL=0
verify() {
  if grep -q "$2" "$FW/$1" 2>/dev/null; then
    echo "   [ok] $3"
  else
    echo "   [MISSING] $3  ($1)"
    VERIFY_FAIL=1
  fi
}
verify "src/SexyAppFramework/graphics/GLPlatform.h"      KHRONOS_APIENTRY         "EGL header fix"
verify "CMakeLists.txt"                                  "PRIVATE NINTENDO_SWITCH" "NINTENDO_SWITCH define"
verify "src/SexyAppFramework/graphics/GLInterface.cpp"   nxp_draw                  "cursor hook"
verify "src/SexyAppFramework/graphics/GLInterface.cpp"   NXGLAD                    "glad diagnostics"
verify "src/SexyAppFramework/platform/switch/Window.cpp" NXEGL                     "EGL diagnostics"
verify "CMakeLists.txt"                                  nx_logc.c                 "logging sources"

if [ "$VERIFY_FAIL" != "0" ]; then
  echo
  echo "Some framework edits did not land. Building now would compile a tree"
  echo "that is only partly patched. Send this output rather than the compiler's."
  exit 1
fi

echo
echo "== setup complete =="
echo
echo "Next: bash build.sh"
