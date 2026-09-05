#!/usr/bin/env python3
"""
instrument-egl.py -- add diagnostics to the framework's Switch EGL setup.

`Failed to initialize OpenGL 2.0` says almost nothing on its own. It is raised
by GLInterface::Init when GLAD_GL_ES_VERSION_2_0 is 0 after
gladLoadGLES2(eglGetProcAddress) -- which can mean the context was never made
current, or the driver came up but glGetString(GL_VERSION) returned NULL, or
eglGetProcAddress resolved nothing at all. Those are different bugs.

The framework cannot distinguish them because MakeWindow() `return`s silently
from every EGL failure path and never checks eglMakeCurrent at all. This adds:

  * the result and eglGetError() of every EGL call
  * an actual check on eglMakeCurrent, which is currently ignored
  * the applet type and free memory, since GPU init depends on both
  * whether eglGetProcAddress resolves a known function, and what
    glGetString(GL_VERSION) returns once the context is supposedly current

One run then says which step failed instead of leaving it to be guessed at.
Everything goes through NxLog, so it lands in insaniquarium_nx.log.
"""
import io
import sys


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    path = sys.argv[1]
    if path.endswith("GLInterface.cpp"):
        return instrument_glinterface(path)

    s = io.open(path, encoding="utf-8", errors="ignore").read()

    if "NXEGL" in s:
        print("   [already applied] EGL diagnostics")
        return 0

    # --- header ------------------------------------------------------------
    anchor = '#include "widget/WidgetManager.h"'
    if anchor not in s:
        print("   [FAILED] could not find the include block in Window.cpp")
        return 1

    header = anchor + '''

/* --- NXEGL diagnostics ---------------------------------------------------
 * Added by port/fixups/instrument-egl.py. Every EGL step below is logged with
 * its result and eglGetError(), because the original code returns silently
 * from each failure path and the resulting message ("Failed to initialize
 * OpenGL 2.0") is the same whichever step went wrong. */
#include "nx_log.h"
#define NXEGL(...) NxLog(__VA_ARGS__)
'''
    s = s.replace(anchor, header, 1)

    # --- per-step logging --------------------------------------------------
    steps = [
        ("\tmWindow = eglGetDisplay(EGL_DEFAULT_DISPLAY);\n\tif (!mWindow)\n\t\treturn;",
         "\tNXEGL(\"egl: applet type %d\", (int)appletGetAppletType());\n"
         "\t{\n\t\tu64 avail = 0, used = 0;\n"
         "\t\tsvcGetInfo(&avail, InfoType_TotalMemorySize, CUR_PROCESS_HANDLE, 0);\n"
         "\t\tsvcGetInfo(&used,  InfoType_UsedMemorySize,  CUR_PROCESS_HANDLE, 0);\n"
         "\t\tNXEGL(\"egl: memory total %llu MB, used %llu MB\",\n"
         "\t\t      (unsigned long long)(avail >> 20), (unsigned long long)(used >> 20));\n\t}\n"
         "\tmWindow = eglGetDisplay(EGL_DEFAULT_DISPLAY);\n"
         "\tNXEGL(\"egl: eglGetDisplay -> %p (err 0x%x)\", (void*)mWindow, eglGetError());\n"
         "\tif (!mWindow)\n\t\treturn;"),

        ("\teglInitialize(mWindow, nullptr, nullptr);",
         "\t{\n\t\tEGLint major = 0, minor = 0;\n"
         "\t\tEGLBoolean ok = eglInitialize(mWindow, &major, &minor);\n"
         "\t\tNXEGL(\"egl: eglInitialize -> %d, version %d.%d (err 0x%x)\",\n"
         "\t\t      (int)ok, (int)major, (int)minor, eglGetError());\n"
         "\t\tif (!ok) { NXEGL(\"egl: FAILED at eglInitialize\"); return; }\n"
         "\t\tconst char *vend = eglQueryString(mWindow, EGL_VENDOR);\n"
         "\t\tconst char *apis = eglQueryString(mWindow, EGL_CLIENT_APIS);\n"
         "\t\tNXEGL(\"egl: vendor '%s', client APIs '%s'\",\n"
         "\t\t      vend ? vend : \"(null)\", apis ? apis : \"(null)\");\n\t}"),

        ("\teglChooseConfig(mWindow, framebufferAttributeList, &config, 1, &numConfigs);",
         "\teglChooseConfig(mWindow, framebufferAttributeList, &config, 1, &numConfigs);\n"
         "\tNXEGL(\"egl: eglChooseConfig -> %d config(s) (err 0x%x)\", (int)numConfigs, eglGetError());"),

        ("\tmSurface = eglCreateWindowSurface(mWindow, config, nwindowGetDefault(), nullptr);",
         "\tmSurface = eglCreateWindowSurface(mWindow, config, nwindowGetDefault(), nullptr);\n"
         "\tNXEGL(\"egl: eglCreateWindowSurface -> %p (err 0x%x)\", (void*)mSurface, eglGetError());"),

        ("\tmContext = eglCreateContext(mWindow, config, EGL_NO_CONTEXT, contextAttributeList);",
         "\tmContext = eglCreateContext(mWindow, config, EGL_NO_CONTEXT, contextAttributeList);\n"
         "\tNXEGL(\"egl: eglCreateContext -> %p (err 0x%x)\", (void*)mContext, eglGetError());"),

        # eglMakeCurrent's result is dropped on the floor upstream. If it fails,
        # nothing is current, glGetString returns NULL, and glad reports no
        # GLES2 -- which is exactly the symptom, with no clue as to the cause.
        ("\teglMakeCurrent(mWindow, mSurface, mSurface, mContext);",
         "\t{\n\t\tEGLBoolean cur = eglMakeCurrent(mWindow, mSurface, mSurface, mContext);\n"
         "\t\tNXEGL(\"egl: eglMakeCurrent -> %d (err 0x%x)\", (int)cur, eglGetError());\n"
         "\t\tif (!cur) NXEGL(\"egl: NOTHING IS CURRENT -- glGetString will return NULL \"\n"
         "\t\t                \"and glad will report no GLES2\");\n"
         "\t\tvoid *p = (void*)eglGetProcAddress(\"glGetString\");\n"
         "\t\tNXEGL(\"egl: eglGetProcAddress(glGetString) -> %p\", p);\n"
         "\t\tif (p) {\n"
         "\t\t\ttypedef const unsigned char *(*GetStr)(unsigned int);\n"
         "\t\t\tconst unsigned char *v = ((GetStr)p)(0x1F02 /* GL_VERSION */);\n"
         "\t\t\tconst unsigned char *r = ((GetStr)p)(0x1F01 /* GL_RENDERER */);\n"
         "\t\t\tNXEGL(\"egl: GL_VERSION '%s'\", v ? (const char*)v : \"(null)\");\n"
         "\t\t\tNXEGL(\"egl: GL_RENDERER '%s'\", r ? (const char*)r : \"(null)\");\n"
         "\t\t}\n\t}"),
    ]

    ok = True
    for old, new in steps:
        if old in s:
            s = s.replace(old, new, 1)
        else:
            print(f"   [FAILED] EGL step not found: {old.strip()[:60]}")
            ok = False

    if not ok:
        return 1

    io.open(path, "w", encoding="utf-8", newline="").write(s)
    print("   [ok] EGL diagnostics added to platform/switch/Window.cpp")
    return 0




def instrument_glinterface(path):
    """
    Log what glad actually did.

    The EGL diagnostics show the context is current and GL is healthy --
    GL_VERSION 'OpenGL ES 3.2 Mesa 20.1.0-rc3', renderer NV12B -- and
    GLInterface::Init still reports no GLES2. glad sets GLAD_GL_ES_VERSION_2_0
    from that same string, and 3 is greater than 2, so the flag should be 1.

    Something between those two facts is untrue and reading the code has not
    found it. So log gladLoadGLES2's return value, the flag immediately after,
    glad's own function pointer, and what THAT pointer returns -- as distinct
    from the one eglGetProcAddress handed back a moment earlier.
    """
    s = io.open(path, encoding="utf-8", errors="ignore").read()

    if "NXGLAD" in s:
        print("   [already applied] glad diagnostics")
        return 0

    anchor = "\t\tinited = true;\n\t\tPlatformGLInit();"
    if anchor not in s:
        print("   [FAILED] could not find PlatformGLInit() in GLInterface::Init")
        return 1

    new_code = (
        "\t\tinited = true;\n"
        "#ifdef NINTENDO_SWITCH\n"
        "\t\t/* NXGLAD: added by port/fixups/instrument-egl.py */\n"
        "\t\t{\n"
        "\t\t\tint gladver = gladLoadGLES2((GLADloadfunc)eglGetProcAddress);\n"
        "\t\t\tNxLog(\"glad: gladLoadGLES2 returned %d\", gladver);\n"
        "\t\t\tNxLog(\"glad: GLAD_GL_ES_VERSION_2_0 = %d\", GLAD_GL_ES_VERSION_2_0);\n"
        "\t\t\tNxLog(\"glad: glad_glGetString = %p\", (void*)glad_glGetString);\n"
        "\t\t\tif (glad_glGetString) {\n"
        "\t\t\t\tconst char *v = (const char*)glad_glGetString(GL_VERSION);\n"
        "\t\t\t\tNxLog(\"glad: its GL_VERSION -> '%s'\", v ? v : \"(null)\");\n"
        "\t\t\t} else {\n"
        "\t\t\t\tNxLog(\"glad: glGetString never loaded -- loader failed early\");\n"
        "\t\t\t}\n"
        "\t\t}\n"
        "#else\n"
        "\t\tPlatformGLInit();\n"
        "#endif")

    s = s.replace(anchor, new_code, 1)

    inc = "#define GLAD_GLES2_IMPLEMENTATION"
    if inc in s and 'include "nx_log.h"' not in s:
        s = s.replace(inc, '#ifdef NINTENDO_SWITCH\n#include "nx_log.h"\n#endif\n' + inc, 1)

    io.open(path, "w", encoding="utf-8", newline="").write(s)
    print("   [ok] glad diagnostics added to graphics/GLInterface.cpp")
    return 0



if __name__ == "__main__":
    sys.exit(main())
