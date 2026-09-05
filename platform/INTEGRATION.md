# Wiring nx_pointer into the Switch backend

nx_pointer is a good fit — it solves the one piece of genuinely new work the
Switch port needed. But it was written for so-loader ports of Android games,
where it is the *only* input owner. The framework's existing
`platform/switch/Input.cpp` also owns the pad and the touchscreen, so dropping
both in unchanged gives you doubled touch events and five colliding buttons.

The `Input.cpp` in this folder is the replacement. What follows is the rest.

## What conflicted, and how it is resolved

**Both owned the touchscreen.** The old backend called
`hidInitializeTouchScreen()` and `hidGetTouchScreenStates()` and synthesised its
own mouse events; `nxp_update()` does the same in `do_touch()`. Running both
means every tap fires twice. nx_pointer now owns touch outright and the old
block is gone.

**All five mapped buttons collided.** The old backend used `+`→Escape,
`-`→Space, `A`→Return, `L`→left click, `R`→right click. nx_pointer claims every
one of those for its own purposes. The new map moves the framework's keys onto
buttons nx_pointer leaves alone.

**Two pad owners.** `nxp_init()` calls `padInitializeDefault()` on its own
`PadState`; `Input.cpp` keeps a second one for B/X/Y/StickL. This is fine — each
`PadState` tracks its own previous frame, so both see correct down/up edges — but
it is worth knowing it is deliberate and not a leftover.

**nx_pointer is silent unless you are tapping.** In `nxp_update()`, cursor events
are only pushed when `phase` is non-zero, and `phase` is only set when the tap
state is held or changing. Move the stick without holding A and nothing is
emitted at all. A mouse-driven UI needs hover: buttons highlight, tooltips
appear, the Insaniquarium tank shows what is under the pointer. `Input.cpp`
therefore polls `nxp_cursor_pos()` each frame and synthesises `MouseMove` when it
changed. This is the single most important part of the integration — without it
the game looks broken in a way that is hard to diagnose, because clicks work
fine.

**No right click.** Insaniquarium branches on `theClickCount < 0` in seven
places (`Board`, `Coin`, `Fish`, `FishTypePet`, `Breeder`, `Grubber`, `Penta`),
so it is not optional. nx_pointer has one tap channel, so B is read from the
second `PadState` and fired at the cursor position as button `-1`.

## Button map

| Input | Action |
| --- | --- |
| A / ZR / ZL | Left click (nx_pointer; ZL/ZR give one-handed play) |
| **B** | **Right click** |
| **X** | **Escape** — back / menu |
| **Y** | **F1** — Auto Collect, same key the PortMaster build binds Y to |
| **StickL click** | **Enter** |
| + | Toggle cursor (nx_pointer) |
| − | Toggle gyro pointing (nx_pointer) |
| L / R | Recenter cursor (nx_pointer) |
| D-pad ↑/↓ | Sensitivity of whatever is driving the cursor (nx_pointer) |
| Touchscreen | Direct pointing, handheld only (nx_pointer) |
| USB mouse | Pointer + left click, handheld or docked (nx_pointer) |

Bold rows are the ones this integration adds. D-pad ←/→ and StickR are still
free.

## Build wiring

**1. Files.** Put `nx_pointer.c` and `nx_pointer.h` next to the backend:

```
src/SexyAppFramework/platform/switch/nx_pointer.c
src/SexyAppFramework/platform/switch/nx_pointer.h
src/SexyAppFramework/platform/switch/Input.cpp    <- replaced
```

`create_flat_directory.sh` excludes `*/platform/*`, so these will not be
symlinked into the flat framework directory and cannot collide with game
headers.

**2. CMakeLists.txt**, in the `if(NINTENDO_SWITCH)` block around line 59:

```cmake
if(NINTENDO_SWITCH)
    set(VORBIS_LIB vorbisfile vorbis)
    list(APPEND SOURCES
        ${CMAKE_CURRENT_SOURCE_DIR}/src/SexyAppFramework/platform/switch/Window.cpp
        ${CMAKE_CURRENT_SOURCE_DIR}/src/SexyAppFramework/platform/switch/Input.cpp
        ${CMAKE_CURRENT_SOURCE_DIR}/src/SexyAppFramework/platform/switch/nx_pointer.c
    )
    list(APPEND PLAT_INCLUDES
        ${CMAKE_CURRENT_SOURCE_DIR}/src/SexyAppFramework/platform/switch
    )
endif()
```

The top-level `project()` already declares `C`, so the `.c` file needs nothing
special.

**3. Link libpng.** nx_pointer decodes an optional `cursor.png`. In the
`elseif (NINTENDO_SWITCH)` link block around line 312:

```cmake
target_link_libraries(pvz-portable PRIVATE GLESv2 EGL glapi drm_nouveau png z)
```

`switch-libpng` and `switch-zlib` are already installed by the CI job, so no
change is needed there.

**4. Draw the cursor.** `nxp_draw()` must run with the engine's GL context
current, immediately before the buffer swap. That is
`GLInterface::Flush()` in `graphics/GLInterface.cpp`, line 1263:

```cpp
void GLInterface::Flush()
{
    gNumVertices = 0;
#ifdef __SWITCH__
    nxp_draw();                 // cursor on top of the finished frame
    eglSwapBuffers(mApp->mWindow, mApp->mSurface);
#else
    SDL_GL_SwapWindow((SDL_Window*)mApp->mWindow);
#endif
```

with, near the top of the file:

```cpp
#ifdef __SWITCH__
extern "C" {
#include "nx_pointer.h"
}
#endif
```

(The `extern "C"` wrapper is unnecessary once the header patch in this folder is
applied.)

## Apply the header patch

`nx_pointer.h` has an include guard but no `extern "C"`, so including it from
C++ mangles the names and every call fails at link time. Both the `Input.cpp`
here and the `Flush()` snippet above work around it with a wrapper, but
`nx_pointer-cxx-and-clamp.patch` fixes it properly, and adds the letterbox
clamp described below.

## Letterboxing

`GLInterface::UpdateViewport()` fits the 4:3 game into the 1280x720 surface,
giving a 960x720 presentation rect at x=160. nx_pointer clamps the cursor to the
full 1280, so the pointer can be walked into the black bars, where `RemapMouse`
produces out-of-range coordinates and clicks do nothing. It reads as a freeze.

With the patch applied, set the insets in `InitInput()`:

```cpp
aConfig.inset_left  = 160;
aConfig.inset_right = 160;
```

Better still, read them from `mGLInterface->mPresentationRect` so the values
follow the viewport instead of being duplicated — but `InitInput()` may run
before the interface exists, so check for null and fall back to 160.

## Resolution: nothing to do

Worth stating explicitly because it looks like it should be a problem.
`nxp_init()` fixes `screen_w`/`screen_h` at startup and there is no
`nxp_set_screen_size()`, so docking mid-game would normally break the clamp and
the cursor scale. It does not here: `Window.cpp` creates the EGL surface from
`nwindowGetDefault()` without setting dimensions, so the surface stays 1280x720
in both modes and the system upscales when docked.
`GLInterface::UpdateViewport()` hardcodes the same pair. If anyone later adds
native 1080p docked rendering, this becomes a real bug and nx_pointer will need
a resize entry point.

## Two things to watch on hardware

**Double cursor.** `InitInput()` calls `EnableCustomCursors(false)` so the
framework does not draw its own pointer under nx_pointer's overlay. If
Insaniquarium turns custom cursors back on when it loads its resources, you will
see two — check there before assuming the overlay is at fault.

**Settings write vs. the loading thread.** `nxp_save_settings()` fires three
seconds after the last sensitivity change and uses plain `fopen`/`fclose`. The
header warns that devkitPro's handle table is not thread-safe and offers
`fopen_fn`/`fclose_fn` hooks for exactly this. This is a native build rather
than a so-loader, so there is no shared table with a foreign engine — but the
framework does run a `WorkerThread` for loading. If you ever see corruption
after nudging the D-pad during a loading screen, pass locked wrappers through
those hooks.

## Suggested order

1. Build the existing Zuma NRO first, unchanged, and confirm it boots. That
   validates devkitA64, GLES2, libopenmpt and NRO packaging before any of this
   is in the picture.
2. Add nx_pointer to *that* build, with the old `Input.cpp` swapped for this
   one. Zuma is also a pointer game, so if the cursor, hover and clicking feel
   right there, the input layer is done and proven.
3. Only then swap the game module for the WinFish decomp.

Debugging input and a fresh game port simultaneously is the thing to avoid — at
step 3 every remaining problem is Insaniquarium-specific.
