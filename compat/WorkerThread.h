/* WorkerThread.h -- portable replacement for the decompilation's Win32 version.
 *
 * The framework has no WorkerThread of its own, so unlike most of this port
 * this is not a substitution but a rewrite. The API is kept identical
 * (DoTask/WaitForTask) so WinFishApp.cpp is untouched; only the two files that
 * implemented it are replaced.
 *
 * The original used two auto-reset events and _beginthread:
 *
 *   DoTask()   waits for the previous task, stores the new one, signals mUnk1
 *   the thread waits on mUnk1, runs the task, signals mUnk2
 *   WaitForTask() waits on mUnk2 -- but only if a task is actually pending
 *
 * Reproduced here with a mutex and two condition variables. The 1000 ms
 * timeouts are kept: the original's waits could expire and the loop re-check,
 * and the thread would exit within a second of mShutdown being set. Dropping
 * them would change shutdown timing, so they stay.
 *
 * Used for exactly one thing -- loading the fish songs off the SD card while
 * the game carries on -- but that one thing runs during startup, so a deadlock
 * here is a hang on a black screen.
 */
#ifndef __SEXY_WORKER_THREAD_H__
#define __SEXY_WORKER_THREAD_H__

#include <condition_variable>
#include <mutex>
#include <thread>

namespace Sexy
{
	class WorkerThread
	{
	public:
		WorkerThread();
		virtual ~WorkerThread();

		/* Blocks until any in-flight task has finished. */
		void WaitForTask();

		/* Waits for the previous task, then queues this one. */
		void DoTask(void (*theTaskProc)(void *), void *theParam);

	private:
		void ThreadProc();

		std::mutex              mMutex;
		std::condition_variable mTaskReady;    /* was mUnk1 */
		std::condition_variable mTaskDone;     /* was mUnk2 */

		void (*mTaskProc)(void *);
		void  *mParam;

		bool   mShutdown;
		std::thread mThread;
	};
}

#endif
