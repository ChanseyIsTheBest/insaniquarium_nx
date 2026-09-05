/* Window.cpp -- entry point, replacing the decompilation's.
 *
 * The original was a WinMain plus a WndProc that returned 0 and was never
 * registered (the framework creates the window itself). Neither has an
 * equivalent here, so the whole file is replaced rather than patched: it is
 * fifteen lines, and surgical edits to an entry point are harder to follow than
 * a rewrite.
 *
 * The one addition is resolving the data directory before the app is built.
 * It has to happen this early because SexyAppBase::Init() reads resources
 * almost immediately, via the ChangeDirHook override in WinFishApp.cpp.
 */
#include "WinFishApp.h"

#ifdef __SWITCH__
#include "nx_datadir.h"
#include "nx_log.h"
#include <cstdio>
#endif

using namespace Sexy;

int main(int argc, char **argv)
{
#ifdef __SWITCH__
	/* Works out which folder under switch/ this was installed into. argv[0] is
	 * the .nro's own path, so the folder name does not matter. */
	NxInitDataDir(argc, argv);

	/* Log goes next to the game data, so it is beside the thing that failed
	 * and is easy to find afterwards. Opened as early as possible: most of
	 * what can go wrong here goes wrong during Init(). */
	NxLogInit(NxGetDataDir());
	NxLog("data dir: %s", NxGetDataDir().c_str());
	if (!NxGetDataDirError().empty())
		NxLog("data dir problem: %s", NxGetDataDirError().c_str());
#else
	(void)argc;
	(void)argv;
#endif

	WinFishApp *aTheApp = new WinFishApp();

	/* Stage markers rather than a running commentary. If the log ends at
	 * "Init", the failure is in resource loading; if it ends at "Start", the
	 * game got as far as its main loop. The last ==> line is where it died. */
#ifdef __SWITCH__
	NxLogStage("Init");
#endif
	aTheApp->Init();

#ifdef __SWITCH__
	NxLogStage("Start");
#endif
	aTheApp->Start();

#ifdef __SWITCH__
	NxLogStage("Shutdown");
#endif
	aTheApp->Shutdown();

	delete aTheApp;

#ifdef __SWITCH__
	NxLogShutdown();
#endif
	return 0;
}
