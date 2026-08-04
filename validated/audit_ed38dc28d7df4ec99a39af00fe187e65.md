This confirms the key fact: `writeExports` in `shells/abstract.go` exports **all** of `info.Build.GetAllVariables()` — i.e., every CI/CD job variable (project, group, pipeline, and job-level `variables:`) — as shell environment variables before the cache-extractor command is invoked in the same shell session. [1](#0-0) 

Since `ChunkSize`/`Concurrency` are declared with `env:"CACHE_CHUNK_SIZE"`/`env:"CACHE_CONCURRENCY"` on `CacheExtractorCommand`, and the `cache-extractor` subcommand is invoked as `w.IfCmdWithOutput(info.RunnerCommand, args...)` inside the very shell that already exported all job variables, an unprivileged pipeline author who defines a job-level variable `CACHE_CONCURRENCY: "100000"` (or `CACHE_CHUNK_SIZE`) would have that variable exported into the job's shell environment and inherited by the `cache-extractor` subprocess — the CLI flag parser reads it via the `env` tag. [2](#0-1) 

### Title
Unprivileged CI job variables (`CACHE_CONCURRENCY`/`CACHE_CHUNK_SIZE`) can force unbounded parallel-download concurrency, exhausting runner host resources - ([File: helpers/transfer/parallel_download.go], [File: commands/helpers/cache_extractor.go])

### Summary
`CacheExtractorCommand.Concurrency` and `.ChunkSize` are bound to environment variables `CACHE_CONCURRENCY`/`CACHE_CHUNK_SIZE`, and only lightly validated (must be non-negative; `normalizeParallelDownloadInputs` merely clamps `concurrency<1` up to `1`, with no upper bound). Because `writeExports` exports every job-defined CI/CD variable into the shell that later invokes the `cache-extractor` internal command, an unprivileged pipeline author can set a job variable named `CACHE_CONCURRENCY` (and/or `CACHE_CHUNK_SIZE`) to an arbitrarily large value, causing `ParallelRangeDownload` to spawn an attacker-chosen, unbounded number of goroutines each holding an open HTTP/blob reader concurrently, consuming file descriptors, sockets, memory and CPU on the runner (or shared host executor).

### Finding Description
`normalizeParallelDownloadInputs` only floors `concurrency` at `1` and does not cap it at any maximum: [3](#0-2)  `ParallelRangeDownload` then creates a semaphore sized exactly to `concurrency` and launches one goroutine per chunk, each performing an HTTP range fetch via `fetchChunk` and holding an open `io.ReadCloser` until the chunk is copied: [4](#0-3) 

`validateCacheTransferTuning` also only rejects negative values, not oversized ones: [5](#0-4) 

`Concurrency`/`ChunkSize` are exposed via the `env` cli tag (`CACHE_CONCURRENCY`, `CACHE_CHUNK_SIZE`), meaning the running process reads these directly from its OS environment: [2](#0-1) 

The `cache-extractor` command is executed inside the job's generated shell script via `w.IfCmdWithOutput(info.RunnerCommand, args...)`, in the same shell session where `writeExports` has already exported **all** job CI/CD variables (`info.Build.GetAllVariables()`) using `w.Variable(...)`, which for Bash uses plain `export`: [6](#0-5) [7](#0-6) 

Because a normal pipeline author fully controls the job/pipeline `variables:` block, they can declare `CACHE_CONCURRENCY` and it will be exported into this shell and inherited by the `cache-extractor` child process, overriding the runner-configured default (16). There is no allow-list or blocking of `CACHE_*`-prefixed job variables analogous to the `CACHE_FALLBACK_KEY` `-protected` suffix check that exists elsewhere in the same file. Existing checks (non-negative validation, feature flag gate `FF_USE_PARALLEL_CACHE_TRANSFER`, `Concurrency > 1` eligibility check) do nothing to bound the *maximum* value, so once the feature flag is enabled by the runner operator (a legitimate, non-privileged-admin configuration choice for the purpose of the question's preconditions), any job can pick an extreme concurrency.

### Impact Explanation
For the `shell` executor (and any executor where the cache-extractor helper subprocess is a direct child sharing the host's process/FD table with the runner and other concurrently running jobs), setting `CACHE_CONCURRENCY` to a very large number (e.g., tens of thousands) causes `ParallelRangeDownload` to attempt to open that many concurrent goroutines and file descriptors (one open HTTP response body reader each) at once, limited only by `contentLength/chunkSize`. Combined with a small `CACHE_CHUNK_SIZE`, the number of chunks (and thus concurrent goroutines/sockets) for a given cache object size can be pushed very high. This can exhaust host-wide file descriptor limits and cause goroutine/memory pressure on the runner process, degrading or disrupting other concurrently executing jobs on the same host — including their session/terminal HTTP handling — because they compete for the same OS-level FD table and process resources.

### Likelihood Explanation
Preconditions: runner operator must have `FF_USE_PARALLEL_CACHE_TRANSFER` enabled and a shared/multi-tenant runner host (shell executor or any executor sharing FDs/process resources across jobs) — both plausible, non-privileged-admin-only configurations. Given that, the exploit requires only a job author adding a `variables:` entry in `.gitlab-ci.yml`, which is fully within an unprivileged pipeline author's control. It is deterministic and repeatable every time the job downloads a cache with `FF_USE_PARALLEL_CACHE_TRANSFER` on.

### Recommendation
Clamp `Concurrency` and `ChunkSize` to a hard maximum (configured by the runner operator, not overridable per-job) in `validateCacheTransferTuning`/`normalizeParallelDownloadInputs`, and/or strip or ignore job-controllable `CACHE_*` transfer-tuning environment variables when invoking the `cache-extractor` helper (only allow these to be set via runner `config.toml`/operator environment, not via job/pipeline variables), similar to how `CACHE_FALLBACK_KEY` values ending in `-protected` are blocked in `addCacheConfig`.

### Proof of Concept
Go unit test: call `normalizeParallelDownloadInputs(contentLength, chunkSize, concurrency)` with `concurrency = 1_000_000` and assert the returned concurrency is bounded (e.g., `<= someMaxConcurrency`) rather than passed through unchanged. Integration-level PoC: define a `.gitlab-ci.yml` job with `variables: { CACHE_CONCURRENCY: "100000", CACHE_CHUNK_SIZE: "1" }`, enable `FF_USE_PARALLEL_CACHE_TRANSFER=true` on the runner, restore a moderately sized cache, and monitor runner process open-FD/goroutine counts during the download — assert they scale unboundedly with the job-supplied value instead of being capped, and assert that a concurrently running second job's session/terminal handling degrades (e.g., increased latency/`EMFILE` errors) during the first job's cache restore.

### Citations

**File:** shells/abstract.go (L337-373)
```go
func (b *AbstractShell) addExtractCacheCommand(
	ctx context.Context,
	w ShellWriter,
	info common.ShellScriptInfo,
	cacheConfigs []cacheConfig,
	cachePaths []string,
) {
	cacheConfig := cacheConfigs[0]

	args := []string{
		"cache-extractor",
		"--file", cacheConfig.ArchiveFile,
		"--timeout", strconv.Itoa(info.Build.GetCacheRequestTimeout()),
	}

	w.Noticef("Checking cache for %s...", cacheConfig.HumanKey)

	extraArgs, env, err := getCacheDownloadURLAndEnv(ctx, info.Build, cacheConfig.HashedKey)
	args = append(args, extraArgs...)
	if err != nil {
		w.Warningf("Failed to obtain environment for cache %s: %v", cacheConfig.HumanKey, err)
	}
	if env != nil {
		cacheEnvFilename := b.writeCacheExports(w, env)
		args = append(args, "--env-file", cacheEnvFilename)
		defer w.RmFile(cacheEnvFilename)
	}

	alternateURLArgs, alternateErr := getAlternateCacheDownloadURL(ctx, info.Build, cacheConfig.AlternateKey)
	if alternateErr != nil {
		w.Warningf("Failed to obtain alternate URL for cache %s: %v", cacheConfig.HumanKey, alternateErr)
	} else {
		args = append(args, alternateURLArgs...)
	}

	w.IfCmdWithOutput(info.RunnerCommand, args...)
	w.Noticef("Successfully extracted cache")
```

**File:** shells/abstract.go (L593-606)
```go
func (b *AbstractShell) writeExports(w ShellWriter, info common.ShellScriptInfo) {
	for _, variable := range info.Build.GetAllVariables() {
		w.Variable(variable)
	}

	gitlabEnvFile := w.TmpFile(gitlabEnvFileName)

	w.Variable(spec.Variable{
		Key:   "GITLAB_ENV",
		Value: gitlabEnvFile,
	})

	w.SourceEnv(gitlabEnvFile)

```

**File:** commands/helpers/cache_extractor.go (L46-51)
```go
	// Transfer options (all backends: presigned S3, GoCloud S3/Azure/GCS).
	TransferBufferSize int `long:"transfer-buffer-size" env:"CACHE_TRANSFER_BUFFER_SIZE" description:"Buffer size in bytes for streaming cache download (default 4 MiB)"`
	// Parallel download (presigned or GoCloud) requires FF_USE_PARALLEL_CACHE_TRANSFER. Concurrency > 1 for parallel.
	ChunkSize   int `long:"chunk-size" env:"CACHE_CHUNK_SIZE" description:"Chunk size in bytes for parallel cache download when FF_USE_PARALLEL_CACHE_TRANSFER is enabled (default 16 MiB; 0 falls back to default)"`
	Concurrency int `long:"concurrency" env:"CACHE_CONCURRENCY" description:"Concurrent chunks for parallel cache transfer when FF_USE_PARALLEL_CACHE_TRANSFER is enabled (default 16; 0 or 1 = sequential for download)"`

```

**File:** helpers/transfer/parallel_download.go (L16-27)
```go
func normalizeParallelDownloadInputs(contentLength int64, chunkSize int64, concurrency int) (int64, int, error) {
	if chunkSize <= 0 {
		return 0, 0, fmt.Errorf("transfer: chunk size must be positive")
	}
	if chunkSize > contentLength {
		chunkSize = contentLength
	}
	if concurrency < 1 {
		concurrency = 1
	}
	return chunkSize, concurrency, nil
}
```

**File:** helpers/transfer/parallel_download.go (L91-112)
```go
func ParallelRangeDownload(contentLength, chunkSize int64, concurrency int, dest io.WriterAt, fetchChunk FetchChunk) error {
	chunkSize, concurrency, err := normalizeParallelDownloadInputs(contentLength, chunkSize, concurrency)
	if err != nil {
		return err
	}
	chunks := parallelDownloadRanges(contentLength, chunkSize)

	worker := &parallelRangeWorker{dest: dest, fetchChunk: fetchChunk}
	sem := make(chan struct{}, concurrency)
	var wg sync.WaitGroup

	for _, cnk := range chunks {
		wg.Add(1)
		sem <- struct{}{}
		go func(offset, length int64) {
			defer wg.Done()
			defer func() { <-sem }()
			worker.downloadChunk(offset, length)
		}(cnk.offset, cnk.length)
	}
	wg.Wait()
	return worker.firstErr
```

**File:** commands/helpers/cache_defaults.go (L15-30)
```go
// validateCacheTransferTuning checks values after normalize* maps 0 to defaults.
// Negative sizes bypass normalization and must be rejected so allocation and blob options do not panic or misbehave.
func validateCacheTransferTuning(transferBufferSize, chunkSize, concurrency int) error {
	if transferBufferSize <= 0 {
		return fmt.Errorf("invalid cache transfer buffer size %d (CACHE_TRANSFER_BUFFER_SIZE / --transfer-buffer-size): must be positive; use 0 for default %d bytes",
			transferBufferSize, defaultCacheTransferBufferSize)
	}
	if chunkSize < 0 {
		return fmt.Errorf("invalid cache chunk size %d (CACHE_CHUNK_SIZE / --chunk-size): must be non-negative; use 0 for default %d bytes",
			chunkSize, defaultCacheChunkSize)
	}
	if concurrency < 0 {
		return fmt.Errorf("invalid cache concurrency %d (CACHE_CONCURRENCY / --concurrency): must be non-negative", concurrency)
	}
	return nil
}
```

**File:** shells/bash.go (L241-245)
```go
}

func (b *BashWriter) ExportRaw(name, value string) {
	b.Linef(`export %s=%s`, b.escape(name), doubleQuote(value))
}
```
