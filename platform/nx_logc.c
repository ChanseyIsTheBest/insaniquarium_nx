/* nx_logc.c -- the log writer, and the entry points nx_crash_handler.c needs.
 *
 * Writes through libnx's fsFileWrite, NOT newlib open/write/FILE.
 *
 * The reason is a crash this port hit three times, each time inside whatever
 * diagnostic had been added to investigate the previous one:
 *
 *     _write_r +0x4c   Data Abort, faulting address 0x20
 *
 * 0x20 is devoptab_t::write_r, so _write_r read a function pointer out of a
 * null devoptab. The mechanism is the one the BTD5 Switch port documents after
 * hitting it too: the loading thread constantly opens and closes asset files --
 * ImageLib probes .png, .jpg, .gif and .tga for every image -- and that mutates
 * newlib's shared stdio and fd tables. Those tables are not under the malloc
 * lock, so a concurrent newlib write() from logging races them and reads a
 * corrupted fd->device slot.
 *
 * Going through fsp-srv directly touches none of newlib's shared state, so it
 * can neither corrupt nor be corrupted by the game's file I/O. One libnx mutex
 * serialises writers; the handle is opened lazily on first use.
 *
 * Text is buffered in RAM and written only when the buffer fills. Writing per
 * line costs a filesystem resize plus an SD sync -- two blocking IPC calls --
 * every time, which is a large permanent cost at any real log rate. Anything
 * that must survive a crash calls NxLogFlush() explicitly.
 */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

/* Logging is OFF unless NX_DEBUG_LOG is defined.
 *
 * Release builds are silent: no log file is created, and every writer above
 * this -- the port's own NxLog lines, the EGL and glad instrumentation, the
 * image-probe reporting, and the framework's whole printf stream, which
 * Common.h routes to Sexy::PrintF and from there to NxLogRaw -- becomes a
 * no-op at a single point rather than each having to be removed.
 *
 * Re-enable with:  DEBUG_LOG=1 bash setup.sh && bash build.sh
 *
 * The functions keep their signatures either way: nx_crash_handler.c calls
 * debug_log_flush by name, and the framework patches call NxLogRaw/NxLogFlush
 * unconditionally, so they must all still link. */
#if defined(__SWITCH__) && !defined(NX_DEBUG_LOG)

#include <stdarg.h>

void NxLogSetPath(const char *thePath) { (void)thePath; }
int  NxLogRaw(const char *theFormat, ...) { (void)theFormat; return 0; }
void NxLogFlush(void) { }
void debug_log_flush(void) { }

#elif defined(__SWITCH__)
#include <switch.h>

#define NXLOG_BUF_SIZE (16 * 1024)
#define NXLOG_GROW     (128 * 1024)

static FsFile s_file;
static s64    s_off = 0;      /* bytes actually written              */
static s64    s_cap = 0;      /* current file size; over-allocated   */
static int    s_ready = 0;    /* 0 = unopened, 1 = open, -1 = failed */
static Mutex  s_mutex;
static char   s_buf[NXLOG_BUF_SIZE];
static size_t s_used = 0;
static char   s_path[512];

/* Caller holds s_mutex. */
static void nxlog_open_locked(void)
{
	FsFileSystem *aFs;
	const char *aPath = s_path;
	const char *aColon;

	if (s_path[0] == '\0')
	{
		s_ready = -1;
		return;
	}

	aFs = fsdevGetDeviceFileSystem("sdmc");
	if (!aFs)
	{
		s_ready = -1;
		return;
	}

	/* The raw fs API wants a path relative to the filesystem root, so strip
	 * the "sdmc:" mount prefix if present. */
	for (aColon = aPath; *aColon && *aColon != ':'; aColon++)
		;
	if (*aColon == ':')
		aPath = aColon + 1;

	fsFsCreateFile(aFs, aPath, 0, 0);          /* no-op if it exists */
	if (R_SUCCEEDED(fsFsOpenFile(aFs, aPath,
	                             FsOpenMode_Write | FsOpenMode_Append, &s_file)))
	{
		fsFileSetSize(&s_file, 0);             /* truncate for a fresh run */
		s_off = 0;
		s_cap = 0;
		s_ready = 1;
	}
	else
	{
		s_ready = -1;
	}
}

/* Caller holds s_mutex. */
static void nxlog_flush_locked(void)
{
	s64 aNeed;

	if (s_ready != 1 || s_used == 0)
		return;

	aNeed = s_off + (s64)s_used;
	if (aNeed > s_cap)
	{
		/* Grow in large steps: a resize per line is a blocking IPC call. */
		s64 aWant = aNeed + NXLOG_GROW;
		if (R_SUCCEEDED(fsFileSetSize(&s_file, aWant)))
			s_cap = aWant;
		else if (R_SUCCEEDED(fsFileSetSize(&s_file, aNeed)))
			s_cap = aNeed;
		else
		{
			s_used = 0;                        /* drop this chunk */
			return;
		}
	}

	if (R_SUCCEEDED(fsFileWrite(&s_file, s_off, s_buf, (u64)s_used,
	                            FsWriteOption_None)))
		s_off += s_used;
	s_used = 0;
}

void NxLogSetPath(const char *thePath)
{
	mutexLock(&s_mutex);
	snprintf(s_path, sizeof s_path, "%s", thePath ? thePath : "");
	/* Re-arm. Without this, a single NxLogRaw before the path was known would
	 * set s_ready to -1 and silently disable logging for the entire run --
	 * and the framework diagnostics can fire from anywhere. */
	s_ready = 0;
	mutexUnlock(&s_mutex);
}

int NxLogRaw(const char *theFormat, ...)
{
	char aLine[1024];
	va_list anArgs;
	int aLen;

	mutexLock(&s_mutex);

	if (s_ready == 0)
		nxlog_open_locked();

	if (s_ready != 1)
	{
		mutexUnlock(&s_mutex);
		return 0;
	}

	va_start(anArgs, theFormat);
	aLen = vsnprintf(aLine, sizeof aLine - 1, theFormat, anArgs);
	va_end(anArgs);

	if (aLen > 0)
	{
		if (aLen > (int)sizeof aLine - 1)
			aLen = (int)sizeof aLine - 1;
		if (s_used + (size_t)aLen > NXLOG_BUF_SIZE)
			nxlog_flush_locked();
		if ((size_t)aLen <= NXLOG_BUF_SIZE)
		{
			memcpy(s_buf + s_used, aLine, (size_t)aLen);
			s_used += (size_t)aLen;
		}
	}

	mutexUnlock(&s_mutex);
	return aLen;
}

void NxLogFlush(void)
{
	mutexLock(&s_mutex);
	nxlog_flush_locked();
	if (s_ready == 1)
	{
		fsFileSetSize(&s_file, s_off);         /* trim the over-allocated tail */
		s_cap = s_off;
		fsFileFlush(&s_file);
	}
	mutexUnlock(&s_mutex);
}

/* nx_crash_handler.c calls this by name before dumping.
 *
 * Try-lock, not lock. If the faulting thread was inside NxLogRaw when it died
 * it still holds s_mutex, and libnx mutexes are not recursive -- a blocking
 * lock here would hang the handler instead of producing the dump. Losing the
 * buffered tail is the better trade. */
void debug_log_flush(void)
{
	if (!mutexTryLock(&s_mutex))
		return;
	nxlog_flush_locked();
	if (s_ready == 1)
	{
		fsFileSetSize(&s_file, s_off);
		s_cap = s_off;
		fsFileFlush(&s_file);
	}
	mutexUnlock(&s_mutex);
}

#else  /* !__SWITCH__ -- host builds, so the callers stay testable */

#include <unistd.h>

void NxLogSetPath(const char *thePath) { (void)thePath; }

int NxLogRaw(const char *theFormat, ...)
{
	char aLine[1024];
	va_list anArgs;
	int aLen;

	va_start(anArgs, theFormat);
	aLen = vsnprintf(aLine, sizeof aLine, theFormat, anArgs);
	va_end(anArgs);
	if (aLen > 0)
	{
		size_t aCount = (size_t)aLen < sizeof aLine ? (size_t)aLen
		                                            : sizeof aLine - 1;
		(void)write(1, aLine, aCount);
	}
	return aLen;
}

void NxLogFlush(void) {}
void debug_log_flush(void) {}

#endif
