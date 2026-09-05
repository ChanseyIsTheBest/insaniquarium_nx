/* WorkerThread.cpp -- see WorkerThread.h. */
#include "WorkerThread.h"

#include <chrono>

using namespace Sexy;

WorkerThread::WorkerThread()
	: mTaskProc(nullptr)
	, mParam(nullptr)
	, mShutdown(false)
{
	mThread = std::thread(&WorkerThread::ThreadProc, this);
}

WorkerThread::~WorkerThread()
{
	/* The original never joined -- the process simply exited. That leaks a
	 * running thread, which on Switch means the applet does not cleanly return
	 * to the menu, so shut it down properly here. */
	{
		std::lock_guard<std::mutex> aLock(mMutex);
		mShutdown = true;
	}
	mTaskReady.notify_all();

	if (mThread.joinable())
		mThread.join();
}

void WorkerThread::WaitForTask()
{
	std::unique_lock<std::mutex> aLock(mMutex);

	/* Matches the original: only wait if something is actually pending, and
	 * give up after a second rather than blocking forever. */
	if (mTaskProc != nullptr)
	{
		mTaskDone.wait_for(aLock, std::chrono::milliseconds(1000),
		                   [this] { return mTaskProc == nullptr || mShutdown; });
	}
}

void WorkerThread::DoTask(void (*theTaskProc)(void *), void *theParam)
{
	WaitForTask();

	{
		std::lock_guard<std::mutex> aLock(mMutex);
		mTaskProc = theTaskProc;
		mParam    = theParam;
	}
	mTaskReady.notify_one();
}

void WorkerThread::ThreadProc()
{
	for (;;)
	{
		void (*aTask)(void *) = nullptr;
		void *aParam = nullptr;

		{
			std::unique_lock<std::mutex> aLock(mMutex);

			mTaskReady.wait_for(aLock, std::chrono::milliseconds(1000),
			                    [this] { return mTaskProc != nullptr || mShutdown; });

			if (mShutdown)
				break;

			if (mTaskProc == nullptr)
				continue;          /* timed out, loop and re-check */

			aTask  = mTaskProc;
			aParam = mParam;
		}

		/* Run the task with the lock released: it reads files off the SD card
		 * and can take a while, and holding the lock would block DoTask() and
		 * WaitForTask() on the main thread for the whole duration. */
		aTask(aParam);

		{
			std::lock_guard<std::mutex> aLock(mMutex);
			mTaskProc = nullptr;
		}
		mTaskDone.notify_all();
	}

	/* Release anyone still in WaitForTask() so shutdown cannot hang. */
	{
		std::lock_guard<std::mutex> aLock(mMutex);
		mTaskProc = nullptr;
	}
	mTaskDone.notify_all();
}
