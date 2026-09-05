/* nx_log.cpp -- see nx_log.h. Formatting only; all writing is in nx_logc.c. */
#include "nx_log.h"

#include <cstdarg>
#include <cstdio>

#ifdef __SWITCH__
#include <switch.h>
#endif

namespace
{
	std::string sPath;

	/* Milliseconds since the log opened. Wall-clock time is not set until the
	 * console has been online, so elapsed time is the more useful number: it
	 * shows where a run stalled. */
	unsigned long Elapsed(void)
	{
#ifdef __SWITCH__
		static u64 sStart = 0;
		const u64 aNow = armTicksToNs(armGetSystemTick()) / 1000000ULL;
		if (sStart == 0)
			sStart = aNow;
		return (unsigned long)(aNow - sStart);
#else
		return 0;
#endif
	}
}

void NxLogInit(const std::string &theDir)
{
	if (theDir.empty())
		return;

	sPath = theDir;
	if (sPath.back() != '/')
		sPath += '/';
	sPath += "insaniquarium_nx.log";

	NxLogSetPath(sPath.c_str());

	NxLogRaw("=== Insaniquarium NX ===\n");
	NxLogRaw("built  " __DATE__ " " __TIME__ "\n");
	NxLogRaw("log    %s\n", sPath.c_str());

	/* Anchor for symbolising crash reports. hbloader maps the NRO as plain
	 * code memory, so Atmosphere's report has no module base to subtract.
	 * Look this symbol's offset up in the .elf with nm, then
	 *     base = this address - that offset
	 * turns every address in the report into a file and line. */
	NxLogRaw("anchor NxLogInit @ %p\n\n", (void *)&NxLogInit);
	NxLogFlush();
}

void NxLog(const char *theFormat, ...)
{
	char aLine[768];
	va_list anArgs;

	va_start(anArgs, theFormat);
	vsnprintf(aLine, sizeof aLine, theFormat, anArgs);
	va_end(anArgs);

	NxLogRaw("[%7lu] %s\n", Elapsed(), aLine);
}

void NxLogStage(const char *theStage)
{
	NxLogRaw("[%7lu] ==> %s\n", Elapsed(), theStage);
	/* Stage markers are exactly what matters when a run dies, so these are
	 * worth the flush that ordinary lines are not. */
	NxLogFlush();
}

void NxLogShutdown(void)
{
	NxLogRaw("\n=== clean exit ===\n");
	NxLogFlush();
}
