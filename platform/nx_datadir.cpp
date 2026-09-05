/* nx_datadir.cpp -- see nx_datadir.h. */
#include "nx_datadir.h"

#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#include <cstdio>
#include <string>
#include <vector>

namespace
{
	std::string sDataDir;
	std::string sError;

	const char *kMarker   = "properties/resources.xml";
	const char *kSwitchDir = "sdmc:/switch";
	const char *kFallback  = "sdmc:/switch/insaniquarium/";

	bool FileExists(const std::string &thePath)
	{
		struct stat aStat;
		return stat(thePath.c_str(), &aStat) == 0 && !S_ISDIR(aStat.st_mode);
	}

	bool DirExists(const std::string &thePath)
	{
		struct stat aStat;
		return stat(thePath.c_str(), &aStat) == 0 && S_ISDIR(aStat.st_mode);
	}

	std::string WithSlash(const std::string &thePath)
	{
		if (thePath.empty())
			return thePath;
		return thePath.back() == '/' ? thePath : thePath + "/";
	}

	/* Does this directory hold the game's data? */
	bool LooksLikeDataDir(const std::string &theDir)
	{
		return FileExists(WithSlash(theDir) + kMarker);
	}

	/* argv[0] is the full path to the .nro, e.g.
	 * "sdmc:/switch/insaniquarium_nx/insaniquarium_nx.nro". Strip the
	 * filename. Note the "sdmc:" prefix contains a colon but no slash, so
	 * searching for the last '/' is safe. */
	std::string DirOfNro(const char *theArgv0)
	{
		if (theArgv0 == nullptr || *theArgv0 == '\0')
			return "";

		const std::string aPath(theArgv0);
		const size_t aSlash = aPath.rfind('/');
		if (aSlash == std::string::npos)
			return "";

		return aPath.substr(0, aSlash + 1);
	}

	/* One level under sdmc:/switch/, looking for the marker. Deliberately not
	 * recursive: the data folder is always a direct child, and walking the
	 * whole SD card at startup would be slow and would risk matching something
	 * that is not ours. */
	std::string ScanSwitchFolder(void)
	{
		DIR *aHandle = opendir(kSwitchDir);
		if (aHandle == nullptr)
			return "";

		std::string aFound;
		while (struct dirent *anEntry = readdir(aHandle))
		{
			const std::string aName = anEntry->d_name;
			if (aName == "." || aName == "..")
				continue;

			const std::string aCandidate = std::string(kSwitchDir) + "/" + aName;
			if (!DirExists(aCandidate))
				continue;

			if (LooksLikeDataDir(aCandidate))
			{
				aFound = WithSlash(aCandidate);
				break;
			}
		}
		closedir(aHandle);

		return aFound;
	}
}

void NxInitDataDir(int argc, char **argv)
{
	sError.clear();

	/* 1. next to the NRO */
	if (argc > 0 && argv != nullptr)
	{
		const std::string aDir = DirOfNro(argv[0]);
		if (!aDir.empty() && LooksLikeDataDir(aDir))
		{
			sDataDir = aDir;
			printf("datadir: %s (from argv[0])\n", sDataDir.c_str());
			return;
		}

		/* The NRO's folder exists but has no data in it. Worth saying so
		 * explicitly: it is almost always someone who copied the .nro across
		 * and forgot the asset folders. */
		if (!aDir.empty())
			sError = "No " + std::string(kMarker) + " in " + aDir;
	}

	/* 2. anywhere under sdmc:/switch/ */
	const std::string aScanned = ScanSwitchFolder();
	if (!aScanned.empty())
	{
		sDataDir = aScanned;
		sError.clear();
		printf("datadir: %s (found by scan)\n", sDataDir.c_str());
		return;
	}

	/* 3. give up, but with a path to name */
	sDataDir = kFallback;
	if (sError.empty())
		sError = "No game data found under " + std::string(kSwitchDir) + "/";
	printf("datadir: %s (fallback -- %s)\n", sDataDir.c_str(), sError.c_str());
}

const std::string &NxGetDataDir(void)
{
	return sDataDir;
}

const std::string &NxGetDataDirError(void)
{
	return sError;
}

bool NxChangeToDataDir(void)
{
	if (sDataDir.empty())
		return false;

	if (!LooksLikeDataDir(sDataDir))
	{
		if (sError.empty())
			sError = "No " + std::string(kMarker) + " in " + sDataDir;
		return false;
	}

	if (chdir(sDataDir.c_str()) != 0)
	{
		sError = "Could not enter " + sDataDir;
		return false;
	}

	return true;
}
