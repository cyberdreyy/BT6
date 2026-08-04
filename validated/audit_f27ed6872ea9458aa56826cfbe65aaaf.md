### Title
Cancelled cache-archiver leaves orphaned `archive_*` temp files because cleanup relies on Go `defer`, which never runs on signal-based process termination - (File: `commands/helpers/cache_archiver.go`)

### Summary
`CacheArchiverCommand.createZipFile` stages the archive in a temp file created with `os.CreateTemp` and relies solely on a deferred `os.Remove(f.Name())` for cleanup, while the archive body is built by `archiver.Archive(context.Background(), c.files)` — a hardcoded non-cancellable context that flows into `archives.CreateZipArchive`, a function that takes no `context.Context` at all and cannot observe cancellation mid-loop. Job cancellation is enforced at the process level (SIGTERM, then SIGKILL after `gracefulKillTimeout`), and because the `cache-archiver` helper installs no signal handler, both signals terminate it before Go's deferred cleanup can run, leaving the partial temp archive on disk.

### Finding Description
`createZipFile` (`commands/helpers/cache_archiver.go:205-251`) does: [1](#0-0) 
- create a uniquely-named temp file via `os.CreateTemp(filepath.Dir(filename), "archive_")`,
- `defer os.Remove(f.Name())` / `defer f.Close()`,
- call `archiver.Archive(context.Background(), c.files)`.

Passing `context.Background()` means the archive step is unconditionally non-cancellable from within the process, regardless of any caller context. For the legacy zip path, `ziplegacy.archiver.Archive` (`commands/helpers/archive/ziplegacy/zip_legacy_archiver.go:35-42`) further drops the `ctx` argument entirely and calls `archives.CreateZipArchive(a.w, sorted)`. [2](#0-1) 
`CreateZipArchive` (`helpers/archives/zip_create.go:85-103`) takes no `context.Context` parameter whatsoever and loops synchronously over `fileNames` with no cancellation checkpoint. [3](#0-2) 

The only external mechanism that can stop this loop is killing the whole `cache-archiver` OS process. GitLab Runner's cancellation implementation (`helpers/process/killer_unix.go:21-44`) sends `SIGTERM` via `Terminate()`, waits `gracefulKillTimeout`, then sends `SIGKILL` via `ForceKill()`. [4](#0-3) 
A search of the `commands/helpers` package (which hosts `cache-archiver`) shows no `signal.Notify`/SIGTERM handling — signal trapping only exists in top-level daemon commands (`commands/multi.go`, `commands/single.go`, `commands/register.go`, etc.), not in the helper subcommand executed for cache archiving. Without an installed handler, a process's default disposition for `SIGTERM` is immediate termination, and `SIGKILL` can never be intercepted in any case. In both cases, Go's deferred statements — including `defer os.Remove(f.Name())` in `createZipFile` — do not execute, because they only run when the goroutine returns normally (or panics and unwinds); OS-level process termination bypasses the Go runtime's defer machinery entirely.

Thus: (1) cancellation cannot stop `CreateZipArchive` mid-loop because the context is disconnected/ignored, and (2) once the process is killed at the OS level to enforce cancellation, the temp-file cleanup that was supposed to handle exactly this case never fires. The invariant "cancelled-job cleanup must not depend on graceful process shutdown" is violated — cleanup depends entirely on it.

### Impact Explanation
Each cancelled job whose cache-archiving stage is interrupted (via SIGTERM/SIGKILL to the helper process) leaves behind a uniquely-named `archive_*` temp file (from `os.CreateTemp`) of arbitrary partial size in `filepath.Dir(filename)` — the runner's cache staging directory on the shared host filesystem. No component of GitLab Runner subsequently scans for or removes these orphans; nothing "reclaims" them automatically. Over repeated cancellations (trivially triggerable by any pipeline author who owns/cancels their own jobs), this causes unbounded accumulation of dead files, degrading available disk space for all jobs/projects sharing that filesystem/runner host, independent of which project caused the leak.

### Likelihood Explanation
This requires only an unprivileged pipeline author cancelling their own job while its cache-archiving stage is running — a standard, always-available user action (UI/API job cancel), with no special runner configuration needed. To make each leaked file larger and increase impact, the attacker can simply cache a large number/size of paths (`cache:paths`) to prolong the archiving window before cancelling. This is trivially repeatable per job run, and the leaked file size is fully attacker-controlled via the cache path list.

### Recommendation
- Have `createZipFile` accept and thread a real cancellable `context.Context` (from the command's request lifecycle) into `archive.NewArchiver(...).Archive(ctx, ...)` instead of hardcoding `context.Background()`.
- Extend `archives.CreateZipArchive` (and the `ziplegacy` archiver wrapper) to accept a `context.Context` and check `ctx.Err()` between file entries so archiving actually stops on cancellation rather than merely being killed.
- Install a signal handler (`signal.Notify` for `SIGTERM`/`SIGINT`) in the `cache-archiver` helper command's `Execute` path that triggers context cancellation and performs synchronous cleanup (`os.Remove` of the in-progress temp file) before exiting, rather than relying solely on Go's `defer`, which cannot run across an unhandled signal-terminated process.
- As a defense-in-depth measure, add a periodic sweep (or startup sweep) of the cache staging directory that removes stale `archive_*` temp files older than a threshold.

### Proof of Concept
Go integration test plan (extends `commands/helpers/cache_archiver_integration_test.go`):
```go
func TestCacheArchiverSigkillLeavesNoOrphanTempFile(t *testing.T) {
    // 1. Build/run the gitlab-runner binary's `cache-archiver` subcommand as a
    //    real subprocess (exec.Command) with a large set of cache paths so
    //    archiving takes long enough to interrupt mid-way.
    cmd := exec.Command(runnerBinary, "cache-archiver", "--file", archivePath, ...)
    require.NoError(t, cmd.Start())

    // 2. Wait until a partial "archive_*" temp file appears in filepath.Dir(archivePath).
    waitForTempFile(t, filepath.Dir(archivePath))

    // 3. Send SIGKILL (simulating ForceKill after graceful timeout expiry).
    require.NoError(t, cmd.Process.Kill())
    _ = cmd.Wait()

    // 4. Assert no "archive_*" files remain in filepath.Dir(archivePath).
    entries, _ := os.ReadDir(filepath.Dir(archivePath))
    for _, e := range entries {
        assert.NotContains(t, e.Name(), "archive_", "orphaned partial archive file left behind after SIGKILL")
    }
}
```
Expected current (buggy) result: the assertion fails — an `archive_*` file remains because the deferred `os.Remove` never ran. After the fix (context-aware cancellation + signal-triggered cleanup), the temp file is removed even under forced termination.

### Citations

**File:** commands/helpers/cache_archiver.go (L211-235)
```go
	f, err := os.CreateTemp(filepath.Dir(filename), "archive_")
	if err != nil {
		return 0, err
	}
	defer os.Remove(f.Name())
	defer f.Close()

	logrus.Debugln("Temporary file:", f.Name())

	switch strings.ToLower(c.CompressionFormat) {
	case string(spec.ArtifactFormatTarZstd):
		c.CompressionFormat = string(spec.ArtifactFormatTarZstd)
	case string(spec.ArtifactFormatZipZstd):
		c.CompressionFormat = string(spec.ArtifactFormatZipZstd)
	default:
		c.CompressionFormat = string(spec.ArtifactFormatZip)
	}

	archiver, err := archive.NewArchiver(archive.Format(c.CompressionFormat), f, c.wd, GetCompressionLevel(c.CompressionLevel))
	if err != nil {
		return 0, err
	}

	// Create archive
	err = archiver.Archive(context.Background(), c.files)
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_archiver.go (L34-42)
```go
// Archive archives all files as new gzip streams.
func (a *archiver) Archive(ctx context.Context, files map[string]os.FileInfo) error {
	sorted := make([]string, 0, len(files))
	for filename := range files {
		sorted = append(sorted, filename)
	}
	sort.Strings(sorted)

	return archives.CreateZipArchive(a.w, sorted)
```

**File:** helpers/archives/zip_create.go (L85-102)
```go
func CreateZipArchive(w io.Writer, fileNames []string) error {
	tracker := newPathErrorTracker()

	archive := zip.NewWriter(w)
	defer func() { _ = archive.Close() }()

	for _, fileName := range fileNames {
		if err := errorIfGitDirectory(fileName); tracker.actionable(err) {
			printGitArchiveWarning("archive")
		}

		err := createZipEntry(archive, fileName)
		if err != nil {
			return err
		}
	}

	return nil
```

**File:** helpers/process/killer_unix.go (L21-44)
```go
func (pk *unixKiller) Terminate() {
	if pk.cmd.Process() == nil {
		return
	}

	err := syscall.Kill(pk.getPID(), syscall.SIGTERM)
	if err != nil {
		pk.logger.Warn("Failed to terminate process:", err)

		// try to kill right-after
		pk.ForceKill()
	}
}

func (pk *unixKiller) ForceKill() {
	if pk.cmd.Process() == nil {
		return
	}

	err := syscall.Kill(pk.getPID(), syscall.SIGKILL)
	if err != nil {
		pk.logger.Warn("Failed to force-kill:", err)
	}
}
```
