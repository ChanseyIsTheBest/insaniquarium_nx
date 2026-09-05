/* nx_datadir.h -- find the game's data directory at runtime.
 *
 * The port must work from whatever folder it was installed into --
 * sdmc:/switch/insaniquarium_nx, sdmc:/switch/insaniquarium, or anything else
 * the user picked. Nothing is hardcoded.
 *
 * Resolution order:
 *
 *   1. The directory the .nro was launched from. hbmenu passes the NRO's own
 *      path as argv[0], so this is the answer in the normal case and it is
 *      exact -- no guessing, no scanning, and it works for a folder name this
 *      port has never heard of.
 *
 *   2. Failing that, scan sdmc:/switch/ one level deep for a directory
 *      containing properties/resources.xml. Some launch paths (the album
 *      applet takeover, forwarders) give no usable argv, and this covers them.
 *      resources.xml is the marker because it is the file the game cannot
 *      start without -- the same one the PortMaster launcher checks for.
 *
 *   3. sdmc:/switch/insaniquarium/, so there is always a concrete path to name
 *      in an error message rather than an empty string.
 *
 * Once found the process working directory is changed to it, which is what
 * lets the decompilation's relative paths ("fishsongs/", "properties/",
 * "userdata/") work untouched.
 */
#ifndef NX_DATADIR_H
#define NX_DATADIR_H

#include <string>

/* Call from main() before the app is constructed. argv[0] is the NRO path. */
void NxInitDataDir(int argc, char **argv);

/* The resolved directory, with a trailing slash. Empty before NxInitDataDir. */
const std::string &NxGetDataDir(void);

/* chdir() to the resolved directory. Returns false if it does not exist or
 * has no properties/resources.xml, which means the assets were never copied
 * across -- the single most common way this port fails to start. */
bool NxChangeToDataDir(void);

/* Human-readable explanation of what went wrong, for the error screen. */
const std::string &NxGetDataDirError(void);

#endif /* NX_DATADIR_H */
