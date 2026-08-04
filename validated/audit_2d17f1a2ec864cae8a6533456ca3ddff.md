## Analog Vulnerability Analysis

**Vuln class mapped:** "Improper Upper Bound Definition" → missing upper-bound validation on a numeric, user-controlled configuration value leading to excessive resource allocation / DoS, analogous to the missing bounds on `equilibriumFee`/`maxFee` in the Solidity report.

### Title
Missing Upper Bound on `CACHE_CHUNK_SIZE` / `CACHE_TRANSFER_BUFFER_SIZE` / `CACHE_CONCURRENCY` CI/CD Variables Enables Memory-Exhaustion DoS - (File: `commands/helpers/cache_defaults.go`)

### Summary
`CACHE_TRANSFER_BUFFER_SIZE`, `CACHE_CHUNK_SIZE`, and `CACHE_CONCURRENCY` are documented, job-settable CI/CD variables consumed by the `gitlab-runner-helper` cache-archiver/cache-extractor binaries. [1](#0-0)  Their only validation is a lower-bound check (`>0` / `>=0`); there is no upper bound at all.

### Finding Description
`validateCacheTransferTuning` rejects negative/zero values but places no ceiling on `transferBufferSize`, `chunkSize`, or `concurrency`: [2](#0-1) 

These unchecked values are then used directly to size an in-memory buffer and to configure the GoCloud multi-part writer: [3](#0-2) 

Because `CACHE_CHUNK_SIZE` and `CACHE_CONCURRENCY` are multiplied together to determine memory usage per the runner's own docs ("Memory use is approximately chunk size x concurrency"), a pipeline author who is not necessarily the runner administrator can set these values arbitrarily large through ordinary CI/CD variables. [4](#0-3) 

### Impact Explanation
A job (attacker-controlled on a shared runner) can set e.g. `CACHE_CHUNK_SIZE` and `CACHE_CONCURRENCY` to extreme values, causing `make([]byte, c.TransferBufferSize)` and the GoCloud writer's `BufferSize`/`MaxConcurrency` to request excessive memory in the helper process. [5](#0-4)  This can exhaust memory on the executor (or host, depending on executor type), causing an OOM crash of the cache-archiver/extractor and, in constrained environments, impacting other concurrently running jobs sharing the same host resources — directly mirroring the "LP user loses funds / functions revert" impact pattern of the original finding (a critical operation fails/DoSes due to unbounded input).

### Likelihood Explanation
Likelihood is moderate to high on shared runners: these are ordinary, documented CI/CD variables, settable by any pipeline author without special runner administration rights, requiring no privileged executor configuration, no Docker socket access, and no trusted-role compromise. The only precondition is that `FF_USE_PARALLEL_CACHE_TRANSFER` is enabled for the concurrency/chunk path (transfer buffer size applies unconditionally to all cache uploads/downloads).

### Recommendation
Add explicit upper bounds (and sane maximums) for `CACHE_TRANSFER_BUFFER_SIZE`, `CACHE_CHUNK_SIZE`, and `CACHE_CONCURRENCY` in `validateCacheTransferTuning`, and/or cap the product of chunk size × concurrency to a safe total memory budget, similar to the `clamp` pattern already used elsewhere in the codebase for other job-supplied variables. [6](#0-5) 

### Proof of Concept
1. In `.gitlab-ci.yml`, set job variables:
```yaml
variables:
  FF_USE_PARALLEL_CACHE_TRANSFER: "true"
  CACHE_CHUNK_SIZE: "9999999999"
  CACHE_CONCURRENCY: "9999999999"
```
2. Trigger a job with `cache:` enabled on a shared runner.
3. `validateCacheTransferTuning` passes (values are positive) at [2](#0-1) , and `writerOpts.BufferSize`/`MaxConcurrency` at [7](#0-6)  attempts to allocate/parallelize far beyond reasonable limits, exhausting memory in the cache-archiver process.

---

Note on scope: this analysis is a defensive-review exercise mapping an unrelated smart-contract bug report onto the GitLab Runner codebase; it should be validated against GitLab's actual `SECURITY.md` scope before being treated as a confirmed, in-scope report, particularly regarding whether cache-transfer resource exhaustion on job-controlled memory is considered in-scope DoS versus expected "self-inflicted" job misconfiguration.

### Citations

**File:** commands/helpers/cache_archiver.go (L52-55)
```go
	// Transfer options (all backends: presigned S3, GoCloud S3/Azure/GCS).
	TransferBufferSize int `long:"transfer-buffer-size" env:"CACHE_TRANSFER_BUFFER_SIZE" description:"Buffer size in bytes for streaming cache upload/download (default 4 MiB)"`
	ChunkSize          int `long:"chunk-size" env:"CACHE_CHUNK_SIZE" description:"Part/chunk size in bytes for GoCloud upload when FF_USE_PARALLEL_CACHE_TRANSFER is enabled (default 16 MiB)"`
	Concurrency        int `long:"concurrency" env:"CACHE_CONCURRENCY" description:"Concurrent parts for GoCloud multipart upload when FF_USE_PARALLEL_CACHE_TRANSFER is enabled (default 16; otherwise 1)"`
```

**File:** commands/helpers/cache_archiver.go (L174-189)
```go
	writerOpts := &blob.WriterOptions{
		Metadata:       c.Metadata,
		BufferSize:     c.ChunkSize,
		MaxConcurrency: c.Concurrency,
	}
	ffLogger := logrus.WithField("name", featureflags.UseParallelCacheTransfer)
	if !featureflags.IsOn(ffLogger, os.Getenv(featureflags.UseParallelCacheTransfer)) {
		writerOpts.MaxConcurrency = 1
	}

	writer, err := b.NewWriter(ctx, objectName, writerOpts)
	if err != nil {
		return err
	}

	buf := make([]byte, c.TransferBufferSize)
```

**File:** commands/helpers/cache_defaults.go (L17-29)
```go
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
```

**File:** docs/configuration/speed_up_job_execution.md (L233-241)
```markdown
#### Cache chunk size and concurrency

Chunk size is the size in bytes of each part or chunk for parallel upload (GoCloud) or parallel download (presigned or GoCloud).
Concurrency is how many chunks run in parallel. Memory use is approximately chunk size x concurrency.

| Variable | Description | Default |
|----------|-------------|---------|
| `CACHE_CHUNK_SIZE` | Chunk size in bytes. For upload (GoCloud backends): limits are backend-dependent (for example, 5 MiB to 5 GiB per part, max 10,000 parts for S3; Azure and GCS have their own limits). For download: 0 = legacy sequential; when concurrency > 1, 16 MiB is used if unset. | Upload: 16 MiB (16777216). Download: 0 (legacy) |
| `CACHE_CONCURRENCY` | Number of concurrent chunks. Upload: GoCloud backends only (S3 with RoleARN, Azure, GCS). Download: 0 or 1 = legacy sequential mode; values greater than 1 = parallel mode (presigned or GoCloud). | Upload: 16. Download: 0 (legacy) |
```

**File:** common/build_settings.go (L134-153)
```go
func (b *Build) validateAttemptSettings() []error {
	var errs []error

	clamp := func(variable *int, varName string) {
		const minAttempts, maxAttempts = 1, 10
		val := max(minAttempts, min(maxAttempts, *variable))
		if val != *variable {
			*variable = val
			errs = append(errs, fmt.Errorf("%s: number of attempts out of the range [%d, %d], clamping to: %d", varName, minAttempts, maxAttempts, *variable))
		}
	}

	clamp(&b.buildSettings.ExecutorJobSectionAttempts, "EXECUTOR_JOB_SECTION_ATTEMPTS")
	clamp(&b.buildSettings.GetSourcesAttempts, "GET_SOURCES_ATTEMPTS")
	clamp(&b.buildSettings.ArtifactDownloadAttempts, "ARTIFACT_DOWNLOAD_ATTEMPTS")
	clamp(&b.buildSettings.RestoreCacheAttempts, "RESTORE_CACHE_ATTEMPTS")
	clamp(&b.buildSettings.SecretsRetrievalAttempts, "SECRETS_RETRIEVAL_ATTEMPTS")

	return errs
}
```
