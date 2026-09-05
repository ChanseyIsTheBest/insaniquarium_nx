/* winfish_compat.cpp -- see winfish_compat.h. */
#ifndef _WIN32

#include "winfish_compat.h"

#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>
#include <string>
#include <vector>

namespace
{
	struct FindState
	{
		std::vector<std::string> mNames;
		size_t                   mIndex;
	};

	/* The decomp writes Win32 paths ("fishsongs\\*.txt"). Nothing on this
	 * platform treats '\' as a separator, so normalise before splitting. */
	std::string NormaliseSeparators(const char *thePath)
	{
		std::string aResult(thePath ? thePath : "");
		for (size_t i = 0; i < aResult.size(); i++)
			if (aResult[i] == '\\')
				aResult[i] = '/';
		return aResult;
	}

	/* Only the "prefix*suffix" shape is supported, which is all the game uses:
	 * "*.txt" and "tpl*.html". A full fnmatch would be more than is needed and
	 * more to get wrong. */
	bool MatchGlob(const std::string &theName, const std::string &thePattern)
	{
		const size_t aStar = thePattern.find('*');
		if (aStar == std::string::npos)
			return theName == thePattern;

		const std::string aPrefix = thePattern.substr(0, aStar);
		const std::string aSuffix = thePattern.substr(aStar + 1);

		if (theName.size() < aPrefix.size() + aSuffix.size())
			return false;
		if (theName.compare(0, aPrefix.size(), aPrefix) != 0)
			return false;
		if (!aSuffix.empty() &&
		    theName.compare(theName.size() - aSuffix.size(), aSuffix.size(), aSuffix) != 0)
			return false;

		return true;
	}
}

HANDLE FindFirstFileA(const char *thePattern, WIN32_FIND_DATAA *theData)
{
	if (thePattern == nullptr || theData == nullptr)
		return INVALID_HANDLE_VALUE;

	const std::string aFull = NormaliseSeparators(thePattern);

	std::string aDir     = ".";
	std::string aPattern = aFull;

	const size_t aSlash = aFull.rfind('/');
	if (aSlash != std::string::npos)
	{
		aDir     = aFull.substr(0, aSlash);
		aPattern = aFull.substr(aSlash + 1);
		if (aDir.empty())
			aDir = "/";
	}

	/* Open a device-qualified path.
	 *
	 * A relative path -- "fishsongs", or "." -- carries no device prefix, and
	 * newlib resolves those through devoptab_list. That lookup can yield NULL
	 * here, and opendir then reads ->diropen_r from it: field offset 0x78 of
	 * devoptab_t, which is exactly the fault that killed the framework's
	 * casepath() before it was fixed the same way. This shim enumerates
	 * "fishsongs/*.txt" and would have hit it next.
	 *
	 * getcwd() returns the sdmc:-qualified directory, so joining onto that
	 * gives opendir something it can resolve. */
	std::string aOpenPath = aDir;
	if (aDir == "." || (!aDir.empty() && aDir[0] != '/' && aDir.find(':') == std::string::npos))
	{
		char aCwd[512];
		if (getcwd(aCwd, sizeof aCwd) != nullptr)
		{
			aOpenPath = aCwd;
			if (!aOpenPath.empty() && aOpenPath.back() != '/')
				aOpenPath += '/';
			if (aDir != ".")
				aOpenPath += aDir;
		}
	}

	DIR *aHandle = opendir(aOpenPath.c_str());
	if (aHandle == nullptr)
		return INVALID_HANDLE_VALUE;

	FindState *aState = new FindState();
	aState->mIndex = 0;

	/* Read the whole directory up front. The alternative -- holding the DIR*
	 * open across the caller's loop -- would keep a descriptor alive while the
	 * game opens and parses each song file inside that same loop. */
	while (struct dirent *anEntry = readdir(aHandle))
	{
		const std::string aName = anEntry->d_name;
		if (aName == "." || aName == "..")
			continue;
		if (MatchGlob(aName, aPattern))
			aState->mNames.push_back(aName);
	}
	closedir(aHandle);

	if (aState->mNames.empty())
	{
		delete aState;
		return INVALID_HANDLE_VALUE;
	}

	snprintf(theData->cFileName, MAX_PATH, "%s", aState->mNames[0].c_str());
	theData->dwFileAttributes = 0;
	aState->mIndex = 1;

	return (HANDLE)aState;
}

BOOL FindNextFileA(HANDLE theHandle, WIN32_FIND_DATAA *theData)
{
	if (theHandle == INVALID_HANDLE_VALUE || theHandle == nullptr || theData == nullptr)
		return FALSE;

	FindState *aState = (FindState *)theHandle;
	if (aState->mIndex >= aState->mNames.size())
		return FALSE;

	snprintf(theData->cFileName, MAX_PATH, "%s", aState->mNames[aState->mIndex].c_str());
	theData->dwFileAttributes = 0;
	aState->mIndex++;

	return TRUE;
}

BOOL FindClose(HANDLE theHandle)
{
	if (theHandle == INVALID_HANDLE_VALUE || theHandle == nullptr)
		return FALSE;

	delete (FindState *)theHandle;
	return TRUE;
}

BOOL DeleteFileA(const char *thePath)
{
	if (thePath == nullptr)
		return FALSE;

	const std::string aPath = NormaliseSeparators(thePath);
	return unlink(aPath.c_str()) == 0 ? TRUE : FALSE;
}

#endif /* !_WIN32 */
