Confirmed: no per-chunk ETag/If-Match validation exists anywhere in this file or `cache_metadata.go`. The `etag` obtained from the single-byte probe request in `tryPresignedParallelDownload` is only used for logging and stored as metadata—never used to pin subsequent range requests via an `If-Match` header, and `presignedRangeFetchChunk` and the GoCloud `fetchChunk` closures issue independent, unauthenticated-consistency HTTP/bucket reads with no cross-check that all chunks originate from the same object version.

### Title
Parallel cache download stitches together bytes from different object versions without ETag/version pinning, unlike the atomic sequential path - ([File: helpers/transfer/parallel_download.go], [File: commands/helpers/cache_extractor.go])

### Summary
`downloadPresignedSequential`/`downloadAndSaveCache` fetch the cache object with a single HTTP GET, so the extracted archive is always a byte-consistent snapshot of one object version. `tryPresignedParallelDownload`/`downloadParallel`/`ParallelRangeDownload`, however, issue one probe request plus N independent Range GET requests to the same URL and assemble the results with `WriteAt` at fixed offsets, without ever verifying that each chunk's response carries the same `ETag`/version as the initial probe.

### Finding Description
In `commands/helpers/cache_extractor.go`, `tryPresignedParallelDownload` performs a `bytes=0-0` probe GET, captures `resp.Header.Get("ETag")` [1](#0-0) , then calls `downloadParallel`, which fans out chunk fetches through `presignedRangeFetchChunk` — each an independent `http.NewRequest`/`c.getClient().Do` with only a `Range` header, no `If-Match` or version pin against the probed ETag [2](#0-1) . `ParallelRangeDownload` in `helpers/transfer/parallel_download.go` runs these fetches concurrently and writes each chunk directly at its byte offset via `dest.WriteAt` with no reconciliation across chunks [3](#0-2) . The GoCloud path has the same structure: `attrs.ETag` is captured once, then `NewRangeReader` is called per chunk with no version binding [4](#0-3) . By contrast, the sequential path reads the whole body from a single response object (`resp.Body` / `reader`) in one `io.CopyBuffer` call, so it is atomic with respect to object mutation on the backend [5](#0-4) . If the underlying cache object at the same URL/key is overwritten between the probe and the last chunk fetch (or between chunk fetches themselves) — which can legitimately happen when multiple jobs/pipelines share a cache key and upload concurrently — the parallel path can splice bytes from two different uploads into a single local archive that no single upload ever produced, while `writeCacheMetadataFile` still records only the first-observed ETag/metadata as if it described the whole file [6](#0-5) .

### Impact Explanation
The runner extracts this spliced/torn archive into the job's build directory as if it were a single validated cache. Since job authors routinely control cache `key:` values (including keys that intentionally collide across branches/pipelines in the same project to share a cache), a pipeline author can race a victim job's parallel cache download by re-uploading a cache to the same key mid-download, causing the victim's `Extractor.Extract` step to unpack content that mixes attacker-supplied bytes with the legitimate cache — all without any integrity check catching the inconsistency, because the archive format itself (zip/tar) may still parse even if internal file bytes came from two different sources depending on where chunk boundaries fall relative to archive entry boundaries.

### Likelihood Explanation
This requires precise timing (winning a race between a competing cache upload and the multi-request chunked download window) and a shared/colliding cache key, so it is not trivially reliable, but it is fully within reach of an ordinary pipeline author who controls `.gitlab-ci.yml` `cache:key` and can schedule concurrent pipelines/jobs. It is specific to the `FF_USE_PARALLEL_CACHE_TRANSFER` feature flag with `Concurrency > 1` [7](#0-6) , so it does not affect runners using the sequential default.

### Recommendation
Bind all per-chunk fetches to the object version observed during the probe: send `If-Match: <etag>` (or the provider-specific equivalent, e.g. S3 `x-amz-version-id`, GCS object generation) on every range request in `presignedRangeFetchChunk` and the GoCloud `fetchChunk` closures, and treat any mismatch/412 response as a hard failure that falls back to (or restarts as) a sequential download rather than silently assembling mixed content.

### Proof of Concept
Go test in `helpers/transfer` (or `commands/helpers`) spinning up an `httptest.Server` whose handler serves version "A" bytes for the initial `bytes=0-0` probe and then, starting from the second Range request onward, switches to version "B" bytes (same length, different content, no ETag check enforced by the mock). Run `downloadPresignedSequential`-equivalent flow against the same server (single GET) and assert it always returns a fully-"A"-or-fully-"B" file; then run `tryPresignedParallelDownload`/`downloadParallel` against the same flipping server and assert the resulting local file contains a mix of "A" and "B" chunk bytes, proving the two code paths diverge for byte-identical adversarial-timing conditions and that no error/retry is triggered by the parallel path.

### Citations

**File:** commands/helpers/cache_extractor.go (L248-251)
```go
func (c *CacheExtractorCommand) presignedParallelDownloadEligible() bool {
	logger := logrus.WithField("name", featureflags.UseParallelCacheTransfer)
	return featureflags.IsOn(logger, os.Getenv(featureflags.UseParallelCacheTransfer)) && c.Concurrency > 1
}
```

**File:** commands/helpers/cache_extractor.go (L279-304)
```go
	contentLength, ok := transfer.ParseContentRangeTotal(resp.Header.Get("Content-Range"))
	if !ok {
		_ = resp.Body.Close()
		return false, nil
	}

	date, _ := time.Parse(http.TimeFormat, resp.Header.Get("Last-Modified"))
	if isLocalCacheFileUpToDate(c.File, date) {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, transfer.RangeProbeBodyMaxDiscard))
		_ = resp.Body.Close()
		logrus.Infoln(filepath.Base(c.File), "is up to date")
		return true, nil
	}

	chunkSize := c.effectiveParallelChunkSize()
	if contentLength <= int64(chunkSize) {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, transfer.RangeProbeBodyMaxDiscard))
		_ = resp.Body.Close()
		return false, nil
	}

	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, transfer.RangeProbeBodyMaxDiscard))
	_ = resp.Body.Close()

	cleanedURL := url_helpers.CleanURL(selectedURL)
	err = c.downloadParallel(contentLength, date, resp.Header.Get("ETag"), cleanedURL, headersToCacheMetadata(resp.Header), c.presignedRangeFetchChunk(selectedURL))
```

**File:** commands/helpers/cache_extractor.go (L308-328)
```go
func (c *CacheExtractorCommand) downloadPresignedSequential() error {
	selectedURL := c.selectPresignedURL()

	resp, err := c.getCache(selectedURL)
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()

	upToDate, date := checkIfUpToDate(c.File, resp)
	if upToDate {
		logrus.Infoln(filepath.Base(c.File), "is up to date")
		return nil
	}

	etag := resp.Header.Get("ETag")
	cleanedURL := url_helpers.CleanURL(selectedURL)
	contentLength := getRemoteCacheSize(resp)

	return c.downloadAndSaveCache(resp.Body, date, etag, cleanedURL, contentLength, headersToCacheMetadata(resp.Header))
}
```

**File:** commands/helpers/cache_extractor.go (L337-354)
```go
func (c *CacheExtractorCommand) presignedRangeFetchChunk(rawURL string) func(offset, length int64) (io.ReadCloser, error) {
	return func(offset, length int64) (io.ReadCloser, error) {
		req, err := http.NewRequest(http.MethodGet, rawURL, nil)
		if err != nil {
			return nil, err
		}
		req.Header.Set("Range", fmt.Sprintf("bytes=%d-%d", offset, offset+length-1))
		resp, err := c.getClient().Do(req)
		if err != nil {
			return nil, err
		}
		if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusPartialContent {
			_ = resp.Body.Close()
			return nil, fmt.Errorf("range request failed: %s", resp.Status)
		}
		return resp.Body, nil
	}
}
```

**File:** commands/helpers/cache_extractor.go (L489-498)
```go
	// Use parallel range reads when FF_USE_PARALLEL_CACHE_TRANSFER is enabled, Concurrency > 1, and backend supports range.
	logger := logrus.WithField("name", featureflags.UseParallelCacheTransfer)
	if featureflags.IsOn(logger, os.Getenv(featureflags.UseParallelCacheTransfer)) && c.Concurrency > 1 && attrs.Size > 0 { //nolint:nestif
		if c.gocloudParallelRangeSupported(ctx, u.Scheme, selectedBucket, selectedObjectName) {
			if attrs.Size > int64(c.effectiveParallelChunkSize()) {
				fetchChunk := func(offset, length int64) (io.ReadCloser, error) {
					return selectedBucket.NewRangeReader(ctx, selectedObjectName, offset, length, nil)
				}
				return c.downloadParallel(attrs.Size, attrs.ModTime, attrs.ETag, cleanedURL, attrs.Metadata, fetchChunk)
			}
```

**File:** commands/helpers/cache_extractor.go (L513-516)
```go
// downloadParallel writes content via concurrent range fetches using WriteAt at chunk offsets (bounded memory); the meter counts bytes via WriteAt. fetchChunk returns a reader for the given byte range; caller closes it.
func (c *CacheExtractorCommand) downloadParallel(contentLength int64, modTime time.Time, etag, cleanedURL string, metadata map[string]string, fetchChunk func(offset, length int64) (io.ReadCloser, error)) error { //nolint:gocognit
	file, err := os.CreateTemp(filepath.Dir(c.File), "cache")
	if err != nil {
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
