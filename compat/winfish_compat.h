/* winfish_compat.h -- Win32 shims for the Insaniquarium decompilation.
 *
 * Force-included into every game translation unit (CMake -include). The point
 * is to leave the decompilation's own code ALONE wherever a small shim can
 * absorb the difference, so port/game/ stays close to upstream and can be
 * regenerated when the decomp is updated.
 *
 * What is shimmed here, and why each one rather than a source edit:
 *
 *   GetTickCount()   used in FishSongMgr for music timing. One-line map onto
 *                    SDL_GetTicks(), which is what the framework itself uses.
 *
 *   DWORD            appears as a type on locals, members and parameters in
 *                    FishSongMgr and WinFishApp. A typedef is far less invasive
 *                    than rewriting every declaration.
 *
 *   FindFirstFileA   the fishsongs loader enumerates "fishsongs\*.txt" with the
 *   FindNextFileA    Win32 API. Reimplemented over dirent so WinFishApp.cpp's
 *   FindClose        loop compiles and runs unchanged, including the backslash
 *                    in the pattern -- separators are normalised in here.
 *
 *   MessageBoxA      one call, a version-info popup. Routed to stdout.
 *
 * NOT shimmed, because a shim would be a lie: the registry, the screensaver
 * installer and BetaSupport's GDI font handling do not have meaningful Switch
 * behaviour. Those are removed or #ifdef'd by port/fixups/apply-portability.py.
 */
#ifndef WINFISH_COMPAT_H
#define WINFISH_COMPAT_H

#ifndef _WIN32

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <sys/stat.h>
#include <SDL2/SDL_timer.h>

/* ------------------------------------------------------------ basic types */

/* CAREFUL: the framework's Common.h already declares DWORD and HWND in its
 * non-Windows branch:
 *
 *     typedef void *HWND, *HFONT;
 *     typedef unsigned int DWORD;
 *
 * This header is force-included, so it is parsed FIRST and Common.h's versions
 * arrive second. A repeated typedef is legal only when both name the SAME
 * type, so DWORD is spelled `unsigned int` here to match exactly rather than
 * `uint32_t` -- those are the same type on aarch64, but relying on that is how
 * a build breaks on some future target for no visible reason. */
typedef unsigned int DWORD;
typedef unsigned long ULONG;
typedef signed int   LONG;
typedef unsigned char BYTE;
typedef unsigned short WORD;
typedef int          BOOL;

#ifndef TRUE
#define TRUE  1
#define FALSE 0
#endif

/* Same as Common.h's, deliberately. HANDLE is ours alone -- the framework only
 * uses it inside its own #ifdef _WIN32 branch. */
typedef void *HWND;
typedef void *HANDLE;

#ifndef MAX_PATH
#define MAX_PATH 260
#endif

/* ------------------------------------------------------------------ time */

/* The framework's clock is SDL's, so the game shares it and the two never
 * drift apart. */
inline DWORD GetTickCount(void)
{
	return (DWORD)SDL_GetTicks();
}

/* --------------------------------------------------------- message boxes */

#define MB_OK               0x0000
#define MB_ICONINFORMATION  0x0040
#define MB_ICONERROR        0x0010

inline int MessageBoxA(HWND, const char *theText, const char *theCaption, unsigned)
{
	printf("[%s] %s\n", theCaption ? theCaption : "message", theText ? theText : "");
	return 1;
}
#define MessageBox MessageBoxA

/* ------------------------------------------------------------- debug log */

/* Three calls in SexyApp::Init() logging product name and build number.
 * nxlink picks stdout up with `nxlink -s`. */
inline void OutputDebugStringA(const char *theText)
{
	if (theText)
		fputs(theText, stdout);
}
#define OutputDebugString OutputDebugStringA

/* ------------------------------------------------------------- user name */

/* Used once, to prefix a screensaver registry path with the Windows account
 * name. There is no such thing here, and returning FALSE makes the caller skip
 * the append entirely -- which is the honest answer and needs no source edit. */
inline BOOL GetUserNameA(char *, DWORD *)
{
	return FALSE;
}
#define GetUserName GetUserNameA

/* ------------------------------------ helpers from the original framework */

/* The framework the decomp shipped with had these; this one does not.

   StringToSexyStringFast wrapped a narrow string into the SexyString type.
   That mattered when SexyString could be std::wstring -- the original header
   even has two definitions, one per build. Here SexyString IS std::string, and
   the original's own non-Unicode definition was already `(x)`, so this is
   exactly what it did there too. */
#ifndef StringToSexyStringFast
#define StringToSexyStringFast(x) (x)
#endif
#ifndef SexyStringToStringFast
#define SexyStringToStringFast(x) (x)
#endif

/* Was `bool CheckForVista();` in the old Common.h, used to pick between
   writing user data beside the .exe (XP) or under ProgramData (Vista+).
   Neither applies, and false takes the simpler branch. Every call site that
   uses the members this port no longer has sits inside `if (CheckForVista())`,
   so returning false makes those branches dead as well as harmless. */
inline bool CheckForVista(void)
{
	return false;
}

/* Used to locate the .exe so its path could be written into the screensaver
   registry keys. There is no module path here and nothing reads those keys, so
   an empty result is the honest answer. */
inline DWORD GetModuleFileNameA(void *, char *theBuffer, DWORD theSize)
{
	if (theBuffer && theSize)
		theBuffer[0] = '\0';
	return 0;
}
#define GetModuleFileName GetModuleFileNameA

/* 8.3 short paths are a Windows filesystem feature with no equivalent.
   Returning 0 means "failed", which callers already handle. */
inline DWORD GetShortPathNameA(const char *, char *theBuffer, DWORD theSize)
{
	if (theBuffer && theSize)
		theBuffer[0] = '\0';
	return 0;
}
#define GetShortPathName GetShortPathNameA

/* ------------------------------------------------------ string comparison */

/* MSVC spells the case-insensitive compare with a leading underscore. The
 * framework's Common.h already maps the unprefixed `stricmp` onto strcasecmp
 * for this platform; six sites in ProfileMgr use the MSVC name. */
#include <strings.h>
#ifndef _stricmp
#define _stricmp  strcasecmp
#endif
#ifndef _strnicmp
#define _strnicmp strncasecmp
#endif

/* ----------------------------------------------------- directory creation */

/* One call, in InternetManager's ad-download path: it walks a relative URL and
 * makes each directory component in turn. mkdir's "already exists" is not an
 * error for that pattern, which is why the return value is ignored the same
 * way CreateDirectoryA's was. */
inline BOOL CreateDirectoryA(const char *thePath, void *)
{
	if (thePath == nullptr || *thePath == '\0')
		return FALSE;
	return ::mkdir(thePath, 0755) == 0 ? TRUE : FALSE;
}
#define CreateDirectory CreateDirectoryA

/* ------------------------------------------------- directory enumeration */

#define INVALID_HANDLE_VALUE ((HANDLE)-1)
#define FILE_ATTRIBUTE_DIRECTORY 0x10

typedef struct
{
	DWORD dwFileAttributes;
	char  cFileName[MAX_PATH];
} WIN32_FIND_DATAA;

typedef WIN32_FIND_DATAA WIN32_FIND_DATA;

/* Implemented in winfish_compat.cpp. Handles the '\' in the decomp's patterns
 * and matches only the simple "prefix*suffix" globs the game actually uses
 * ("fishsongs\*.txt", "temp\tpl*.html"). */
HANDLE FindFirstFileA(const char *thePattern, WIN32_FIND_DATAA *theData);
BOOL   FindNextFileA(HANDLE theHandle, WIN32_FIND_DATAA *theData);
BOOL   FindClose(HANDLE theHandle);
BOOL   DeleteFileA(const char *thePath);

#define FindFirstFile FindFirstFileA
#define FindNextFile  FindNextFileA
#define DeleteFile    DeleteFileA

#endif /* !_WIN32 */

#endif /* WINFISH_COMPAT_H */
