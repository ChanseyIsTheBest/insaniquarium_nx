# Insaniquarium Deluxe — Nintendo Switch port (SexyAppFramework, native)
 
This is a **native** Switch port of Insaniquarium Deluxe, built from the
[WinFish](https://github.com/vindirect/winfish) decompilation on top of the
[PvZ-Portable](https://github.com/kyle-sylvestre/PvZ-Portable) SexyAppFramework
runtime. It contains no game assets — you supply those from your own copy.
 
## Install & run
 
You need the data folders from a retail install of Insaniquarium Deluxe.
 
Put the `.nro` in any folder under `sdmc:/switch/` and place your game files
next to it — the loader finds its own folder at runtime, so the name is up to
you:
 
```
sdmc:/switch/insaniquarium
├── insaniquarium_nx.nro
├── properties/                            <- must contain resources.xml
├── data/  images/  music/  sounds/  fishsongs/
└── cursor.png                             <- optional
```
 
Launch from hbmenu, or via title override (hold **R** while starting an
installed game) for the full heap.

## Controls
 
| Input | Action |
| --- | --- |
| **+** | Toggle the on-screen cursor |
| **–** | Toggle gyro pointing (tilt/turn the controller to aim) |
| **Left stick** | Move the cursor |
| **L / R** | Recenter the cursor to the middle of the screen (helps gyro aiming) |
| **A / ZR / ZL** | Click at the cursor (ZL and ZR let you play one-handed) |
| **B** | Right click — sells fish and feeds pets |
| **X** | Escape / back out of menus |
| **Y** | Auto-collect toggle |
| **Stick click** | Enter / confirm |
| **D-pad up / down** | Adjust sensitivity of whatever is driving the cursor |
 
The cursor is on by default when docked and off in handheld; **+** overrides
either way. The touchscreen is always live in handheld. A USB mouse works in
both modes: move to control the cursor, left-click to tap, and use the scroll
wheel to change sensitivity — gyro turns itself off while a mouse is connected.
Right click is on **B** only; the mouse's right button is not wired up.
Your stick, mouse and gyro sensitivities are remembered in `pointer.cfg`
automatically after in-game adjustment.
 
## Saves
 
Profiles and saved games go in `userdata/` next to the `.nro`, in the original
game's format — they are byte-compatible with the PC version, so you can move
them either way.
 
## Building
 
Requires devkitPro with the `switch-dev` group plus these portlibs:
 
```sh
pacman -S switch-dev
pacman -S switch-sdl2 switch-libogg switch-libvorbis switch-mpg123 \
          switch-libpng switch-libjpeg-turbo switch-zlib switch-cmake \
          switch-mesa switch-libdrm_nouveau
```
 
Then:
 
```sh
export DEVKITPRO=/opt/devkitpro
bash setup.sh               # clones upstreams, derives the port
bash build.sh               # -> out/insaniquarium_nx.nro
```
 
`setup.sh` clones the decompilation and the framework, applies the port's
fixups, and grafts the result into the framework tree. It is safe to re-run;
every edit converges from any previous version. `build.sh` refuses to build a
tree that does not match the package.
 
Release builds are silent — no log file is written. For diagnostics:
 
```sh
DEBUG_LOG=1 bash setup.sh && bash build.sh   # writes insaniquarium_nx.log
```
 
`libopenmpt` is built from source during `build.sh`, as devkitPro has no
package for it. `switch-sdl2_mixer` must **not** be removed.
 
## Credits
 
The game is decompiled by [vindirect](https://github.com/vindirect/winfish).
The runtime is [kyle-sylvestre](https://github.com/kyle-sylvestre/PvZ-Portable)'s
SexyAppFramework port, `zuma` branch. The virtual cursor (`nx_pointer`) and the
diagnostic scaffolding derive from the open-source Switch homebrew lineage —
Andy Nguyen, fgsfds and ChanseyIsTheBest, building on TheOfficialFloW's
Vita/Switch loader tradition — reaching this project via the Bouncemasters and
BTD5 ports. The Insaniquarium PortMaster port supplied the decompilation bug
fixes. All MIT-licensed. Thanks to everyone in that lineage.
