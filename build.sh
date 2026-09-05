#!/usr/bin/env bash
# build.sh -- build the NRO with devkitA64.
#
# Follows the framework's own Switch CI job step for step, including the two
# non-obvious bits: libopenmpt is built from source because devkitPro has no
# package for it, and switch-sdl2_mixer / switch-mpg123 are REMOVED after
# installing, because their presence breaks the link. That is upstream's
# workaround, reproduced rather than reinvented.
set -euo pipefail

# Hold the window open if this was double-clicked rather than run from a shell.
# Without it the script prints why it stopped and the terminal closes on the
# same tick, so the message is never seen and the failure looks like "it does
# nothing". Only pauses when stdin is a terminal, so CI is unaffected.
on_exit() {
  local rc=$?
  if [ $rc -ne 0 ]; then
    echo
    echo "---------------------------------------------------------------"
    echo " build.sh stopped (exit $rc). The reason is printed above."
    echo "---------------------------------------------------------------"
    if [ -t 0 ]; then
      echo
      read -r -p "Press Enter to close..." _
    fi
  fi
}
trap on_exit EXIT

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FW="$ROOT/external/framework"

if [ ! -d "$FW" ]; then
  echo "Nothing to build yet: $FW does not exist."
  echo
  echo "setup.sh has to run first -- it clones the framework and the"
  echo "decompilation, derives the port and grafts it into the tree:"
  echo
  echo "    bash setup.sh"
  echo
  exit 1
fi

if [ -z "${DEVKITPRO:-}" ]; then
  echo "DEVKITPRO is not set, so this is not a devkitPro shell."
  echo
  echo "Use the same terminal you built the Zuma NRO in. On Windows that is"
  echo "the devkitPro MSYS2 shell, not Command Prompt or stock Git Bash."
  echo "On Linux/WSL, try:"
  echo
  echo "    source /etc/profile.d/devkit-env.sh"
  echo
  exit 1
fi

SKIP_DEPS="${SKIP_DEPS:-0}"

if [ "$SKIP_DEPS" != "1" ]; then
  echo "== 1. devkitPro packages =="

  # The package manager has two names. On Linux and macOS devkitPro ships its
  # own binary as dkp-pacman so it cannot collide with a system pacman; inside
  # the Windows MSYS2 environment it IS the system pacman. Neither is wrong.
  if command -v dkp-pacman >/dev/null 2>&1; then
    PKG=dkp-pacman
  elif command -v pacman >/dev/null 2>&1; then
    PKG=pacman
  else
    PKG=""
  fi

  if [ -z "$PKG" ]; then
    echo "   no pacman found -- skipping the package step."
    echo "   That is usually fine: if the Zuma NRO built, these are installed."
    echo "   Step 2 below will fail clearly if anything is actually missing."
  else
    echo "   using $PKG"
    # --needed makes this a no-op when everything is present. Non-fatal on
    # purpose: a refusal here (no elevation, locked db) should not stop a build
    # whose dependencies are already satisfied.
    $PKG -S --needed --noconfirm \
      switch-sdl2 switch-libogg switch-libvorbis switch-mpg123 \
      switch-libpng switch-libjpeg-turbo switch-zlib switch-cmake \
      || echo "   (package install did not complete -- continuing)"
  fi

  # NOTE: an earlier version of this script removed switch-sdl2_mixer and
  # switch-mpg123 here, copied from the CI of a DIFFERENT fork. Do not put that
  # back. This branch's CMakeLists has
  #     find_library(MPG123_LIBRARY NAMES mpg123 libmpg123 REQUIRED)
  # so removing switch-mpg123 makes configure fail outright. The branch being
  # built here has no Switch CI to copy from.
  #
  # If the LINK stage later reports duplicate SDL_mixer symbols, that is the
  # clash the other fork was working around -- SDL-Mixer-X is built from source
  # as a subdirectory and the switch-sdl2_mixer portlib can collide with it.
  # Remove just that one, by hand, and re-run:
  #     $PKG -R switch-sdl2_mixer

  echo
  echo "== 2. libopenmpt (no devkitPro package; built from source) =="
  # shellcheck disable=SC1091
  source "$DEVKITPRO/switchvars.sh"
  export PKG_CONFIG_SYSROOT_DIR=
  export PKG_CONFIG_LIBDIR="$DEVKITPRO/portlibs/switch/lib/pkgconfig"

  if [ -f "$DEVKITPRO/portlibs/switch/lib/libopenmpt.a" ] \
     && [ -f "$DEVKITPRO/portlibs/switch/include/libopenmpt/libopenmpt.h" ]; then
    echo "   already built"
  else
    rm -rf "$ROOT/external/openmpt"
    git clone --depth 1 --branch libopenmpt-0.8.9 \
        https://github.com/OpenMPT/openmpt "$ROOT/external/openmpt"
    # The NO_* flags matter here. libopenmpt's Makefile autodetects optional
    # dependencies, and on a cross build it will happily find the HOST's
    # libraries and try to link them into an aarch64 static library. Turning
    # each one off explicitly is the difference between a clean build and a
    # pile of architecture-mismatch errors.
    # -j matters here: 153 translation units and ~122k lines of heavily
    # templated C++. Without it this is a single-core build while the rest of
    # the machine idles, which on MSYS (where every compiler spawn pays fork
    # emulation) is the difference between minutes and a lot of minutes.
    make -C "$ROOT/external/openmpt" -j"$(nproc)" \
      CONFIG=gcc CC=aarch64-none-elf-gcc CXX=aarch64-none-elf-g++ AR=aarch64-none-elf-ar \
      SHARED_LIB=0 STATIC_LIB=1 DYNLINK=0 EXAMPLES=0 OPENMPT123=0 TEST=0 \
      NO_MPG123=1 NO_OGG=1 NO_VORBIS=1 NO_VORBISFILE=1 NO_FLAC=1 \
      NO_SNDFILE=1 NO_PORTAUDIO=1 NO_PORTAUDIOCPP=1 NO_PULSEAUDIO=1 NO_SDL2=1 \
      PREFIX="$PORTLIBS_PREFIX" install
  fi
fi

echo
echo "== 3. build =="
# shellcheck disable=SC1091
source "$DEVKITPRO/switchvars.sh"
export PKG_CONFIG_SYSROOT_DIR=
export PKG_CONFIG_LIBDIR="$DEVKITPRO/portlibs/switch/lib/pkgconfig"

# --- libopenmpt is not optional -------------------------------------------
# Insaniquarium's soundtrack is three .mo3 module files (music/Insaniq2.mo3,
# music/Alien.mo3, music/Lullaby.mo3). Nothing else can play them. Without
# libopenmpt the build SUCCEEDS and the game runs with sound effects and total
# silence for music, which is a miserable thing to discover on hardware.
#
# Checked even under SKIP_DEPS: skipping the work is reasonable, skipping the
# check is how you end up with a silent build.
OPENMPT_LIB="$DEVKITPRO/portlibs/switch/lib/libopenmpt.a"
OPENMPT_INC="$DEVKITPRO/portlibs/switch/include"

if [ ! -f "$OPENMPT_LIB" ] || [ ! -f "$OPENMPT_INC/libopenmpt/libopenmpt.h" ]; then
  echo
  echo "libopenmpt is not installed for Switch."
  echo "  library: $OPENMPT_LIB $([ -f "$OPENMPT_LIB" ] && echo '(found)' || echo '(MISSING)')"
  echo "  headers: $OPENMPT_INC/libopenmpt/libopenmpt.h $([ -f "$OPENMPT_INC/libopenmpt/libopenmpt.h" ] && echo '(found)' || echo '(MISSING)')"
  echo
  echo "Insaniquarium's music is .mo3 module files and libopenmpt is the only"
  echo "thing here that can decode them. Building without it gives you a game"
  echo "with sound effects and no music at all."
  echo
  if [ "$SKIP_DEPS" = "1" ]; then
    echo "You used SKIP_DEPS=1, which skips building it. Re-run without that:"
    echo
    echo "    bash build.sh"
    echo
  else
    echo "Step 2 should have built it -- check the output above for errors."
    echo
  fi
  echo "To build anyway and accept silent music, set MUSIC_OPTIONAL=1."
  echo
  [ "${MUSIC_OPTIONAL:-0}" = "1" ] || exit 1
  echo "MUSIC_OPTIONAL=1 set -- continuing without music."
  OPENMPT_ARGS=""
else
  echo "   libopenmpt: found"
  # SDL-Mixer-X has its own FindOpenMPT that wants the header directory as
  # well as the library, and it searches only for the name "openmpt". The
  # framework's own find_library accepts "libopenmpt" too, so it is possible
  # for the framework to be satisfied while SDL-Mixer-X quietly disables MO3
  # support -- which is exactly what "OpenMPT is missing, will be disabled"
  # in its summary means. Passing both explicitly removes the guesswork.
  OPENMPT_ARGS="-DOpenMPT_LIBRARY=$OPENMPT_LIB -DOpenMPT_INCLUDE_DIR=$OPENMPT_INC"
fi

# --- is the grafted tree current? ------------------------------------------
# Running build.sh without setup.sh after updating the package produces a build
# from the PREVIOUS package's sources, which looks like a successful build of
# the current one. That has now cost several diagnostic cycles: a log arrives
# without the instrumentation that was just added, and the obvious reading --
# "the probe ran and found nothing" -- is the wrong one.
#
# Compare what the package has against what is grafted, and stop if they differ.
STALE=""
for f in Input.cpp nx_pointer.c nx_datadir.cpp nx_log.cpp nx_logc.c nx_crash_handler.c; do
  src="$ROOT/platform/$f"
  dst="$FW/src/SexyAppFramework/platform/switch/$f"
  if [ ! -f "$dst" ]; then
    STALE="$STALE\n     missing:   $f"
  elif ! cmp -s "$src" "$dst"; then
    STALE="$STALE\n     differs:   $f"
  fi
done

# Every framework edit leaves a marker. Absence means that patch never ran --
# and setup.sh aborting partway through leaves a tree that builds, so the
# failure surfaces as a compile error in a system header rather than as
# anything pointing at setup.
#
# This list previously covered only the two instrumentation patches, which is
# why an unapplied KHRONOS_APIENTRY fix sailed through and reproduced an EGL
# header error that had supposedly been fixed.
check_patch() {
  grep -q "$2" "$FW/$1" 2>/dev/null || STALE="$STALE\n     not patched: $1 ($3)"
}
check_patch "src/SexyAppFramework/platform/switch/Window.cpp"  NXEGL             "EGL diagnostics"
check_patch "src/SexyAppFramework/graphics/GLInterface.cpp"    NXGLAD            "glad diagnostics"
check_patch "src/SexyAppFramework/graphics/GLInterface.cpp"    "nxp_draw"        "on-screen cursor hook"
check_patch "src/SexyAppFramework/graphics/GLPlatform.h"       KHRONOS_APIENTRY  "EGL header fix"
check_patch "CMakeLists.txt"     "PRIVATE NINTENDO_SWITCH"     "NINTENDO_SWITCH define"
check_patch "CMakeLists.txt"     "src/WinFish"                 "game module path"
check_patch "CMakeLists.txt"     "nx_logc.c"                   "logging sources"
# The fix for the root-cause crash, and the patches that made it findable. A
# build that lacks gLoadingThread still calls std::thread::detach(), which
# throws here and takes the process down -- and it produced an identical crash
# report to the one that had just been diagnosed, because this list did not
# know to refuse the stale tree.
check_patch "src/SexyAppFramework/SexyAppBase.cpp"             "gLoadingThread"          "loading thread stored, not detached"
check_patch "src/SexyAppFramework/SexyAppBase.cpp"             "FATAL ERROR (popup)"     "popup logging"
check_patch "src/SexyAppFramework/fcaseopen/fcaseopen.c"       "nx_qualified_opendir"    "fcaseopen device qualification"
check_patch "src/SexyAppFramework/fcaseopen/fcaseopen.c"       "!defined(__SWITCH__)"    "casepath fallback disabled"
check_patch "src/SexyAppFramework/Common.cpp"                  "NxLogRaw"                "PrintF routed through the safe writer"
check_patch "src/SexyAppFramework/imagelib/ImageLib.cpp"       "tried %-40s"             "image probe reporting"
check_patch "src/SexyAppFramework/SexyAppBase.cpp"             "nxp_set_cursor_slot"     "game cursors -> virtual pointer"
check_patch "src/SexyAppFramework/SexyAppBase.cpp"             "eglDestroyContext"       "EGL teardown on exit"
check_patch "src/SexyAppFramework/platform/switch/nx_pointer.c" "NXP_GAME_CURSOR_SCALE"  "game cursor scale"
check_patch "CMakeLists.txt"     "Insaniquarium Deluxe"        "NRO title"
check_patch "CMakeLists.txt"     "ChanseyIsTheBest"            "NRO author"
check_patch "CMakeLists.txt"     "icon-insaniquarium.jpg"      "NRO icon"

if [ -n "$STALE" ]; then
  echo
  echo "The build tree does not match this package:"
  printf "%b\n" "$STALE"
  echo
  echo "Run setup.sh first -- otherwise this builds the previous package's"
  echo "sources, and the result looks like a successful build of this one."
  echo
  echo "    bash setup.sh && bash build.sh"
  echo
  exit 1
fi
echo "   build tree matches the package"

# --- generator ---------------------------------------------------------------
# Ninja rather than Unix Makefiles, because of this:
#
#     compiler_depend.make:4: *** multiple target patterns.  Stop.
#
# Under MSYS the toolchain lives at C:/devkitPro/... and CMake writes those
# absolute paths into the generated dependency files. GNU make parses a rule as
# "target: prerequisite", sees the colon in "C:/devkitPro/...", and reads the
# drive letter as a target of its own. It surfaces in SDL-Mixer-X first simply
# because that subproject is built early. Ninja has no such ambiguity and
# handles Windows paths correctly, so switching generator removes the whole
# class of problem rather than dodging it by picking a path spelling that
# happens to work today.
if command -v ninja >/dev/null 2>&1; then
  GENERATOR="Ninja"
else
  GENERATOR="Unix Makefiles"
  echo
  echo "   ninja is not installed, falling back to Unix Makefiles."
  echo "   On MSYS that can fail with:"
  echo "       *** multiple target patterns.  Stop."
  echo "   because make reads the colon in C:/devkitPro/... as a rule separator."
  echo "   If that happens, install ninja and re-run:"
  echo
  echo "       pacman -S ninja"
  echo
fi
echo "   generator: $GENERATOR"

# --- stale build directory -------------------------------------------------
# MSYS reaches the same folder as both C:/devkitPro/... and /opt/devkitpro/...,
# so the cached source path and the current one are routinely different STRINGS
# for the same DIRECTORY. Comparing them as text wipes a perfectly good build
# tree and forces a full rebuild -- which is what an earlier version of this
# script did. Resolve both and compare the real locations instead.
same_dir() {
  [ -d "$1" ] && [ -d "$2" ] || return 1
  [ "$(cd "$1" 2>/dev/null && pwd -P)" = "$(cd "$2" 2>/dev/null && pwd -P)" ]
}

if [ -f "$FW/build/CMakeCache.txt" ]; then
  CACHED_SRC=$(grep -m1 "^CMAKE_HOME_DIRECTORY:" "$FW/build/CMakeCache.txt" | cut -d= -f2-)
  CACHED_GEN=$(grep -m1 "^CMAKE_GENERATOR:" "$FW/build/CMakeCache.txt" | cut -d= -f2-)

  if [ -n "$CACHED_SRC" ] && ! same_dir "$CACHED_SRC" "$FW"; then
    echo "   build tree came from a different source dir -- wiping"
    rm -rf "$FW/build"
  elif [ -n "$CACHED_GEN" ] && [ "$CACHED_GEN" != "$GENERATOR" ]; then
    # CMake refuses to switch generator in place, so this one must be a wipe.
    echo "   generator changed ($CACHED_GEN -> $GENERATOR) -- wiping build tree"
    rm -rf "$FW/build"
  fi
fi

# Force a recompile of every framework file setup.sh edits.
#
# Two consecutive builds shipped a binary WITHOUT the loading-thread fix while
# the source file on disk had it. The compiled object was stale, and the build
# system did not notice. Deleting the objects makes their recompilation certain.
for f in SexyAppBase.cpp imagelib/ImageLib.cpp Common.cpp graphics/ImageFont.cpp \
         fcaseopen/fcaseopen.c graphics/GLInterface.cpp platform/switch/Window.cpp; do
  touch "$FW/src/SexyAppFramework/$f" 2>/dev/null
  rm -f "$FW/build/CMakeFiles/pvz-portable.dir/src/SexyAppFramework/${f%.*}.o" 2>/dev/null
done

mkdir -p "$FW/build"
cd "$FW/build"
# shellcheck disable=SC2086
aarch64-none-elf-cmake .. -G "$GENERATOR" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS_RELEASE=-O2 $OPENMPT_ARGS
# Generator-agnostic, so this works for both branches above.
cmake --build . --parallel "$(nproc)"

# Verify the binary actually contains the fix. Checking the source is not
# enough -- that is exactly what let two stale builds through.
ELF="$FW/build/insaniquarium_nx.elf"
if [ -f "$ELF" ]; then
  if ! aarch64-none-elf-nm -C "$ELF" 2>/dev/null | grep -q "gLoadingThread"; then
    echo
    echo "---------------------------------------------------------------"
    echo " The compiled binary does NOT contain the loading-thread fix."
    echo " SexyAppBase.o was built from an unpatched source. Wipe the build"
    echo " tree and rebuild:"
    echo "     rm -rf external/framework/build"
    echo "     bash setup.sh && bash build.sh"
    echo "---------------------------------------------------------------"
    exit 1
  fi
  echo "   verified: binary contains the loading-thread fix"
fi

NRO="$FW/build/insaniquarium_nx.nro"
if [ -f "$NRO" ]; then
  mkdir -p "$ROOT/out"
  cp "$NRO" "$ROOT/out/"
  echo
  echo "== built: out/insaniquarium_nx.nro =="
  echo
  echo "Copy it, and your game's data folders, to any folder under switch/:"
  echo
  echo "   sdmc:/switch/insaniquarium_nx/insaniquarium_nx.nro"
  echo "   sdmc:/switch/insaniquarium_nx/properties/"
  echo "   sdmc:/switch/insaniquarium_nx/data/  images/  music/  sounds/  fishsongs/"
  echo
  echo "The folder name does not matter -- see README.md."
else
  echo "no .nro produced; check the output above"
  exit 1
fi
