### Title
Uncleaned temporary cache file on job cancellation - persistent disk leak in shared cache directory (`commands/helpers/cache_extractor.go`)

### Summary
`downloadParallel` and `downloadAndSaveCache` rely on a Go `defer os.Remove(tmpName)` to clean up the temporary file created by `os.CreateTemp(filepath.Dir(c.File), "cache")`. This cleanup only runs if the Go process unwinds normally; when a job is cancelled or times out, GitLab Runner terminates the `cache-extractor` helper process with `SIGTERM`/`SIGKILL` at the OS level, and the process (which installs no signal handler) is killed by the Go runtime's default signal action before any deferred code executes, leaving the partially-downloaded temp file behind.

### Finding Description
`c.download` → `handlePresignedURL`/`handleGoCloudURL` → `downloadParallel`/`downloadAndSaveCache` creates a temp file with [1](#0-0) , and the analogous sequential path with [2](#0-1) . Both rely purely on the deferred `os.Remove(tmpName)` for cleanup, with no `signal.Notify`/context-based interception inside the `cache-extractor` command itself, and `Execute` performs no such setup either [3](#0-2) .

`cache-extractor` is invoked as a separate OS process (helper binary) by every executor. When a job is cancelled or hits its timeout, the executor sends `SIGTERM` to the process and, after `graceful_kill_timeout`, `SIGKILL`, as implemented uniformly across executors, e.g. the shell executor's abort path [4](#0-3)  using `newProcessKillWaiter(...).KillAndWait`, and the underlying killer sends raw OS signals to the process group [5](#0-4) . The `cache-extractor` binary (built via `apps/gitlab-runner-helper/main.go` / the runner's own binary) never registers a signal handler for `SIGTERM`, so the Go runtime's default behavior applies: the process is terminated immediately, deferred functions (including `os.Remove(tmpName)`) never run. The comment in `functions/concrete/concrete.go` explicitly documents this exact failure mode for the Kubernetes executor case ("SIGKILL could leave orphan files for the next job to inherit") [6](#0-5) , confirming this is a known, real behavior rather than a theoretical one — though the existing `FF_CLEAN_UP_FAILED_CACHE_EXTRACT` mitigation only removes the *declared cache paths* after a failed extraction, not the leaked `os.CreateTemp` files inside the cache directory itself.

An unprivileged pipeline author controls: (1) the cache key (`cacheOptions.Key`), which determines `filepath.Dir(c.File)`, i.e. the directory the leaked temp file lands in; (2) the size of the cache blob being downloaded (their own artifacts/cache content), which bounds how large a partially-written temp file can grow before cancellation; and (3) the timing of job cancellation (manual cancel via UI/API, or triggering `job timeout`/`--cache-download-timeout`). Repeating "start large-cache job → cancel mid-download" leaves one orphaned temp file per iteration on the executor host's cache filesystem.

### Impact Explanation
Each cancelled mid-transfer download leaves a partially-written file named `cache<random>` under `filepath.Dir(c.File)` that is never cleaned up by any subsequent job (no other code path scans for and removes stray `cache*` temp files in that directory). If the cache directory/volume is shared across projects on the same executor host (common for shell executors and self-managed shared runners with a common builds/cache disk or NFS-backed cache), repeated leaks consume shared disk quota and can cause `ENOSPC` failures for unrelated projects' subsequent jobs on the same host/volume — a persistent, multi-tenant disk-exhaustion condition that survives job cancellation and is not remediated by normal job/cache lifecycle cleanup.

### Likelihood Explanation
Fully feasible for any user who can define a `cache:` entry with attacker-controlled size and cancel/timeout their own jobs — no special privileges are needed. It is deterministic given the documented signal-kill semantics (`docs/executors/custom.md` describes SIGTERM→SIGKILL on job cancellation), and repeatable indefinitely since nothing bounds or garbage-collects orphaned `cache*` temp files.

### Recommendation
Track and remove leaked temp files: either (a) have the `cache-extractor` command install a `signal.Notify`-based handler that cancels an internal context and guarantees `os.Remove` executes before exit, or (b) have the parent Runner process perform a best-effort sweep/removal of stale `cache*` temp files older than a threshold in the cache directory before/after each job, or (c) write temp files to a per-job-scoped subdirectory that the executor's normal cleanup step (`RemoveAll`) already tears down after cancellation, instead of directly into the shared cache directory.

### Proof of Concept
Go integration test:
1. Start `CacheExtractorCommand.downloadParallel` (or `downloadAndSaveCache`) against a slow/stalling HTTP test server serving a large `Content-Length`.
2. Run the download inside a subprocess (mirroring real deployment) and after some bytes have been written, send `syscall.SIGTERM` (not close via context) directly to the subprocess, without giving it a chance to catch it.
3. Assert that after the subprocess exits, `filepath.Dir(c.File)` still contains a `cache*` file (i.e., `os.Remove` never ran), demonstrating the leak.
4. Repeat N times and assert the leaked byte count grows unbounded (`sum of leaked file sizes ~= N * partial_download_size`), proving disk usage is not bounded by any cleanup logic.

### Citations

**File:** commands/helpers/cache_extractor.go (L515-522)
```go
	file, err := os.CreateTemp(filepath.Dir(c.File), "cache")
	if err != nil {
		return err
	}
	tmpName := file.Name()
	defer func() {
		_ = os.Remove(tmpName)
	}()
```

**File:** commands/helpers/cache_extractor.go (L567-574)
```go
	file, err := os.CreateTemp(filepath.Dir(c.File), "cache")
	if err != nil {
		return err
	}
	tmpName := file.Name()
	defer func() {
		_ = os.Remove(tmpName)
	}()
```

**File:** commands/helpers/cache_extractor.go (L618-644)
```go
func (c *CacheExtractorCommand) Execute(cliContext *cli.Context) {
	log.SetRunnerFormatter()

	c.normalizeExtractorArgs()
	if err := validateCacheTransferTuning(c.TransferBufferSize, c.ChunkSize, c.Concurrency); err != nil {
		logrus.Fatalln(err)
	}

	wd, err := os.Getwd()
	if err != nil {
		logrus.Fatalln("Unable to get working directory")
	}

	if c.File == "" {
		warningln("Missing cache file")
	}

	if c.URL != "" || c.GoCloudURL != "" {
		err := c.doRetry(c.download)
		if err != nil {
			warningln(err)
		}
	} else {
		logrus.Infoln(
			"No URL provided, cache will not be downloaded from shared cache server. " +
				"Instead a local version of cache will be extracted.")
	}
```

**File:** executors/shell/shell.go (L124-132)
```go
	// Support process abort
	select {
	case err = <-waitCh:
		return err
	case <-cmd.Context.Done():
		logger := common.NewProcessLoggerAdapter(s.BuildLogger)
		return newProcessKillWaiter(logger, s.Config.GetGracefulKillTimeout(), s.Config.GetForceKillTimeout()).
			KillAndWait(c, waitCh)
	}
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

**File:** functions/concrete/concrete.go (L6-21)
```go
//   - FF_CLEAN_UP_FAILED_CACHE_EXTRACT (issue #36988, MR !4565):
//     The abstract shell removes the user-declared cache paths after a
//     failed extraction to recover from a partially-extracted directory
//     left behind by an OOM-killed cache-extractor process. That
//     originated with the Kubernetes executor running cache-extractor in
//     a separate (memory-constrained) helper container, where SIGKILL
//     could leave orphan files for the next job to inherit.
//
//     Concrete runs cache-extractor inside the build environment that
//     step-runner is itself executing in, so a SIGKILL of the extractor
//     almost certainly takes the surrounding context with it; the
//     orphaned-partial-extract failure mode the FF was protecting
//     against does not have a clear analog here. The behaviour is also
//     wrong: it removes pre-existing files in the cache path (e.g.
//     files dropped by git clone or a prior step) along with the
//     partial extract.
```
