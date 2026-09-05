#!/usr/bin/env bash
# port.sh -- regenerate port/game/ from the pristine decompilation.
#
# external/winfish is NEVER modified. This script deletes port/game and rebuilds
# it on every run, so the decomp can be updated from upstream and the port
# re-derived on top. Everything the port changes lives in this script and in
# port/fixups/ -- if you find yourself editing port/game/ directly, the change
# belongs in a fixup instead or it will be lost on the next run.
#
# Idempotent: safe to run repeatedly. Each fixup reports [ok],
# [already applied] or [FAILED].
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${WINFISH_SRC:-$ROOT/external/winfish/source/WinFish}"
DST="$ROOT/port/game"

if [ ! -d "$SRC" ]; then
  echo "missing $SRC"
  echo "run setup.sh first, or set WINFISH_SRC to your own checkout"
  exit 1
fi

echo "== 1. copying the decompilation =="
rm -rf "$DST"
mkdir -p "$DST"
cp "$SRC"/*.cpp "$SRC"/*.h "$DST"/ 2>/dev/null
# .vcxproj/.rc/.ico are Windows build artefacts; CMake replaces them.
rm -f "$DST"/*.vcxproj "$DST"/*.filters
echo "   $(ls "$DST" | wc -l) files"

echo
echo "== 2. game bug fixes (from the upstream PortMaster port) =="
# FIRST, against the pristine decompilation, which is the state these were
# written for. Several later fixups touch the same lines -- the cheat-code loop
# bound and the userdata save path -- and will report [already applied] once
# these have run. The reverse order would leave them failing on patterns that
# no longer exist.
python3 "$ROOT/port/fixups/apply-gamefixes.py"
GAMEFIX_RC=$?

echo
echo "== 3. framework renames =="
python3 "$ROOT/port/fixups/apply-framework.py"
FRAMEWORK_RC=$?

echo
echo "== 3. portability =="
python3 "$ROOT/port/fixups/apply-portability.py"
PORT_RC=$?

echo
echo "== 3b. C++ strictness and 64-bit correctness =="
python3 "$ROOT/port/fixups/apply-cxx-modern.py"
CXX_RC=$?

echo
echo "== 4. entry point and portable WorkerThread =="
# Window.cpp is replaced rather than patched: the original is a WinMain plus a
# WndProc that returns 0 and is never registered, and it also has to gain the
# data-directory lookup.
cp "$ROOT/port/fixups/Window.cpp" "$DST/Window.cpp"
echo "   Window.cpp replaced with the portable main()"

# The framework has no WorkerThread, so unlike the rest of the port this is a
# rewrite rather than a substitution. Same API, so WinFishApp.cpp is untouched.
rm -f "$DST/WorkerThread.cpp" "$DST/WorkerThread.h"
cp "$ROOT/compat/WorkerThread.h" "$ROOT/compat/WorkerThread.cpp" "$DST"/
echo "   WorkerThread.{h,cpp} replaced with the std::thread version"

echo
echo "== 5. sanity: conditionals with no body =="
# Removing a line that was the body of an if leaves the if dangling over the
# next statement. It compiles cleanly and the bug is very hard to spot later,
# so it is checked for explicitly rather than hoped about.
DANGLING=$(grep -nE '^[[:space:]]*(if|else if|for|while)[[:space:]]*\(.*\)[[:space:]]*$' -A1 \
             "$DST"/*.cpp "$DST"/*.h 2>/dev/null \
           | grep -cE '^[^:]+-[0-9]+-[[:space:]]*$')
if [ "$DANGLING" -gt 0 ]; then
  echo "   !! $DANGLING conditional(s) followed by a blank line -- check these:"
  grep -nE '^[[:space:]]*(if|else if|for|while)[[:space:]]*\(.*\)[[:space:]]*$' -A1 \
       "$DST"/*.cpp "$DST"/*.h 2>/dev/null \
    | grep -B1 -E '^[^:]+-[0-9]+-[[:space:]]*$' \
    | grep -E '^[^-]+:[0-9]+:' | sed 's|^.*/game/|   |' | head -10
else
  echo "   ok, none"
fi

echo
echo "== 6. anything Win32 left behind =="
# Uses a preprocessor-aware checker rather than grep: after the fixups run, the
# screensaver and registry code is still present but wrapped in #ifdef _WIN32.
# Grep would report all of it and bury the real problems.
python3 "$ROOT/port/fixups/check-leftovers.py"
LEFTOVER_RC=$?

echo
if [ $GAMEFIX_RC -eq 0 ] && [ $FRAMEWORK_RC -eq 0 ] && [ $PORT_RC -eq 0 ] && [ $CXX_RC -eq 0 ] && [ $LEFTOVER_RC -eq 0 ]; then
  echo "== done. output in $DST =="
else
  echo "== done, BUT something needs attention above. =="
  exit 1
fi
