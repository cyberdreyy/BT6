## Analysis

The core mechanism is: `CACHE_CONCURRENCY` is read directly as a job-controlled int via `env:"CACHE_CONCURRENCY"` on `CacheExtractorCommand.Concurrency`/`CacheArchiverCommand.Concurrency`, and `validateCacheTransferTuning` only rejects negative values — there is no upper bound. [1](#0-0) [2](#0-1) 

This value flows into `transfer.ParallelRangeDownload`, which creates a semaphore-bounded worker pool sized exactly to `concurrency` (`sem := make(chan struct{}, concurrency)`), spawning one goroutine per chunk but never running more than `concurrency` goroutines/HTTP requests at once: [3](#0-2) 

Importantly, `concurrency` is further capped by the number of actual chunks (`contentLength / chunkSize`), since `parallelDownloadRanges` only produces as many byte ranges as needed to cover the object — so real in-flight connections = `min(CACHE_CONCURRENCY, numChunks)`, not `CACHE_CONCURRENCY` unconditionally. To get e.g. 10,000 concurrent connections, the attacker would also need a cache object large enough (`numChunks ≥ CACHE_CONCURRENCY`, i.e. object size ≥ `CACHE_CONCURRENCY × CACHE_CHUNK_SIZE`), which is bounded by realistic cache sizes and `MaxUploadedArchiveSize`/timeout limits, but is not otherwise prevented by the runner.

Each `*http.Client`/`*CacheClient` created in `prepareTransport` uses a fresh `http.Transport` with no `MaxConnsPerHost` set, so Go's default has no hard cap on concurrent outbound connections per host beyond what the caller's own goroutine limit imposes: [4](#0-3) 

So the actual bound on parallel sockets opened by a single cache-extractor/cache-archiver invocation is the `concurrency` value passed into `ParallelRangeDownload`, not any limit in the transport. Since `CACHE_CONCURRENCY` (attacker-controlled via job/CI variables) has no upper clamp — unlike `FASTZIP_EXTRACTOR_CONCURRENCY`, which builder.go explicitly clamps to `[0,128]` via `variables.DefaultIntClamp`: [5](#0-4) 

there's a real asymmetry: `FASTZIP_EXTRACTOR_CONCURRENCY` (used for local extraction) is clamped 0–128, but `CACHE_CONCURRENCY` (used for the parallel-transfer HTTP path, gated behind `FF_USE_PARALLEL_CACHE_TRANSFER`) is not clamped at all in `cache_extractor.go`/`cache_archiver.go`/`cache_defaults.go`. A job author can set `CACHE_CONCURRENCY` to an arbitrarily large integer (e.g. 100000) with `FF_USE_PARALLEL_CACHE_TRANSFER=true` and a cache object large enough to have that many chunks, opening a correspondingly large number of simultaneous outbound HTTP connections to the backend from that single cache-extractor/cache-archiver process.

On job cancellation mid-transfer, `ParallelRangeDownload` has no `context.Context`/cancellation wiring at all — it relies purely on `sync.WaitGroup` completion of in-flight `worker.downloadChunk` calls; there's nothing to explicitly bound this differently in the cancellation case (each connection eventually times out via the `30s` dial/response-header timeouts on the shared transport, or completes/fails independently), so cancellation doesn't change the peak connection count already opened, though it doesn't multiply it further either.

### Title
CACHE_CONCURRENCY has no upper bound, allowing large per-job HTTP connection fan-out to the cache backend - (File: commands/helpers/cache_extractor.go, commands/helpers/cache_archiver.go, commands/helpers/cache_defaults.go)

### Summary
`CACHE_CONCURRENCY` (job-controlled via CI variables) is only validated to be non-negative, unlike the analogous `FASTZIP_EXTRACTOR_CONCURRENCY` which is hard-clamped to `[0,128]` in `builder.go`. When `FF_USE_PARALLEL_CACHE_TRANSFER` is enabled and the cache object is large enough to produce many chunks, a job can drive `transfer.ParallelRangeDownload`'s worker semaphore to an arbitrarily large size, opening a correspondingly large number of simultaneous connections to the cache backend from a single job's cache-extractor/cache-archiver process.

### Finding Description
`CacheExtractorCommand.Concurrency` and `CacheArchiverCommand.Concurrency` are populated from the job environment variable `CACHE_CONCURRENCY` [1](#0-0) [6](#0-5) . The only sanity check applied is `validateCacheTransferTuning`, which rejects negative values but places no ceiling on the value [7](#0-6) .

This value is passed straight into `transfer.ParallelRangeDownload`, which builds a semaphore channel of exactly that capacity and launches one goroutine per chunk gated by the semaphore [8](#0-7) . Each in-flight chunk performs its own `http.Request`/`Do` call via `presignedRangeFetchChunk` or a GoCloud `NewRangeReader`, each of which opens a new outbound connection on the client's `http.Transport`, which itself sets no `MaxConnsPerHost` [4](#0-3) . Since the number of chunks is `ceil(contentLength / chunkSize)`, an attacker who also controls `CACHE_CHUNK_SIZE` (also job-controlled, minimum-clamped only against 0) can push the number of parallel connections up to `CACHE_CONCURRENCY`, limited only by cache object size. Existing checks — the `builder.go` clamp for `FASTZIP_EXTRACTOR_CONCURRENCY` — do not apply to this variable/path at all, since `CACHE_CONCURRENCY` and `CACHE_CHUNK_SIZE` are read directly by the `cache-extractor`/`cache-archiver` helper binaries via `go-flags` `env` tags, bypassing `builder.go`'s clamp logic entirely.

### Impact Explanation
On a shared runner host running multiple concurrent jobs from different projects, one job can cause its cache-extractor or cache-archiver helper process to open a very large number of simultaneous TCP connections/goroutines to the cache object-storage backend. This can exhaust host-level file descriptors, ephemeral ports, or goroutine/memory resources, degrading or starving cache transfers (and potentially other network operations) for concurrently scheduled jobs from other projects on the same runner host — matching the scoped impact of host-level connection/goroutine exhaustion.

### Likelihood Explanation
Requires `FF_USE_PARALLEL_CACHE_TRANSFER=true` (feature flag, off by default but settable per-job via CI variables) and a cache object large enough to yield many chunks at the chosen `CACHE_CHUNK_SIZE`. Both `CACHE_CONCURRENCY` and `CACHE_CHUNK_SIZE` are ordinary job/pipeline variables settable by any pipeline author with no special privilege, making this readily reproducible by an unprivileged CI user, though it does require deliberately staging or referencing a sufficiently large cache object and enabling the feature flag.

### Recommendation
Clamp `CACHE_CONCURRENCY` (and reasonably bound `CACHE_CHUNK_SIZE`) in `validateCacheTransferTuning` (or in `cache_extractor.go`/`cache_archiver.go` normalization) to a fixed maximum analogous to the `[0,128]` clamp already applied to `FASTZIP_EXTRACTOR_CONCURRENCY` in `functions/concrete/builder/builder.go`, and/or set `MaxConnsPerHost` on the `CacheClient` transport in `commands/helpers/cache_client.go` to enforce a hard per-process connection ceiling independent of user input.

### Proof of Concept
Go unit test extending `helpers/transfer/parallel_download_test.go`:
```go
func TestParallelRangeDownload_ConcurrencyUnbounded(t *testing.T) {
    var maxInFlight, inFlight int32
    var mu sync.Mutex
    fetchChunk := func(offset, length int64) (io.ReadCloser, error) {
        mu.Lock()
        inFlight++
        if inFlight > maxInFlight {
            maxInFlight = inFlight
        }
        mu.Unlock()
        time.Sleep(10 * time.Millisecond) // simulate network RTT
        mu.Lock()
        inFlight--
        mu.Unlock()
        return io.NopCloser(bytes.NewReader(make([]byte, length))), nil
    }
    f, _ := os.CreateTemp(t.TempDir(), "parallel-range")
    // large content, tiny chunk, huge attacker-supplied concurrency
    const contentLength = 10 * 1024 * 1024
    const chunkSize = 1024
    const attackerConcurrency = 10000
    _ = transfer.ParallelRangeDownload(contentLength, chunkSize, attackerConcurrency, f, fetchChunk)
    // Assert no hard ceiling is enforced independent of attacker input:
    assert.Greater(t, maxInFlight, int32(128), "expected concurrency to exceed a safe fixed ceiling like 128, proving no upper bound is enforced")
}
```
Expected result: `maxInFlight` scales with `attackerConcurrency` (up to the number of chunks), confirming there is no hard upper bound comparable to the `[0,128]` clamp applied elsewhere (e.g. `FASTZIP_EXTRACTOR_CONCURRENCY`), and that a single job can drive very high transient connection counts limited only by cache object size and its chosen chunk size.

### Citations

**File:** commands/helpers/cache_extractor.go (L49-50)
```go
	ChunkSize   int `long:"chunk-size" env:"CACHE_CHUNK_SIZE" description:"Chunk size in bytes for parallel cache download when FF_USE_PARALLEL_CACHE_TRANSFER is enabled (default 16 MiB; 0 falls back to default)"`
	Concurrency int `long:"concurrency" env:"CACHE_CONCURRENCY" description:"Concurrent chunks for parallel cache transfer when FF_USE_PARALLEL_CACHE_TRANSFER is enabled (default 16; 0 or 1 = sequential for download)"`
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

**File:** helpers/transfer/parallel_download.go (L91-113)
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
}
```

**File:** commands/helpers/cache_client.go (L23-36)
```go
func (c *CacheClient) prepareTransport() {
	c.Transport = &http.Transport{
		Proxy: http.ProxyFromEnvironment,
		DialContext: (&net.Dialer{
			Timeout:   30 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   10 * time.Second,
		ExpectContinueTimeout: 10 * time.Second,
		ResponseHeaderTimeout: 30 * time.Second,
		DisableCompression:    true,
	}
}
```

**File:** functions/concrete/builder/builder.go (L225-230)
```go
			Timeout:                         variables.DefaultIntClamp(b.variables, "CACHE_REQUEST_TIMEOUT", 10, 1, 120),
			Concurrency:                     variables.DefaultIntClamp(b.variables, "FASTZIP_EXTRACTOR_CONCURRENCY", 0, 0, 128),
			Paths:                           cache.Paths,
			MaxAttempts:                     variables.DefaultIntClamp(b.variables, "RESTORE_CACHE_ATTEMPTS", 1, 1, 10),
			UseExponentialBackoffStageRetry: b.isFeatureFlagOn(featureflags.UseExponentialBackoffStageRetry),
		})
```

**File:** commands/helpers/cache_archiver.go (L55-55)
```go
	Concurrency        int `long:"concurrency" env:"CACHE_CONCURRENCY" description:"Concurrent parts for GoCloud multipart upload when FF_USE_PARALLEL_CACHE_TRANSFER is enabled (default 16; otherwise 1)"`
```
