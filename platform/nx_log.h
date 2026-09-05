/* nx_log.h -- the port's log.
 *
 * Everything goes through nx_logc.c, which writes via libnx's fsFileWrite
 * rather than newlib stdio. See that file for why: the game's loading thread
 * opens and closes asset files constantly, which mutates newlib's shared fd
 * tables, and a concurrent newlib write from logging faults in _write_r.
 *
 * That means printf must NOT be used for anything that can run off the main
 * thread. In this framework that is a wider net than it looks: Common.h does
 *     #define printf(...) Sexy::PrintF(__VA_ARGS__)
 * so every printf in framework code formats into a std::string and goes
 * through stdio.
 */
#ifndef NX_LOG_H
#define NX_LOG_H

#include <string>

#ifdef __cplusplus
extern "C" {
#endif

/* Thread-safe, mutex-guarded, buffered. Safe from any thread. */
int  NxLogRaw(const char *theFormat, ...);

/* Push the RAM buffer to the card. Called at shutdown, and by the crash
 * handler (by the name debug_log_flush) before it dumps. */
void NxLogFlush(void);
void NxLogSetPath(const char *thePath);

#ifdef __cplusplus
}
#endif

/* Open the log in theDir. Call once, after the data directory is resolved. */
void NxLogInit(const std::string &theDir);

/* Timestamped line. */
void NxLog(const char *theFormat, ...);

/* Lifecycle marker, so a truncated log still shows how far the run got. */
void NxLogStage(const char *theStage);

void NxLogShutdown(void);

#endif /* NX_LOG_H */
