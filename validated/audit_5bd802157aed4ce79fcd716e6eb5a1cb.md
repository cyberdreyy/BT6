### Title
Parallel range download does not pin object version/ETag across chunk requests, allowing cache splicing - (File: commands/helpers/cache_extractor.go)

### Summary
`presignedRangeFetchChunk` (used by `tryPresignedParallelDownload` → `downloadParallel` → `transfer.ParallelRangeDownload`) issues each chunk as an independent `GET` with only a `Range` header, and the GoCloud parallel path (`handleGoCloudURL`) calls `selectedBucket.NewRangeReader(ctx, selectedObjectName, offset, length, nil)` with no conditional options. Neither path pins the object to the ETag/version observed during the initial existence/size probe, so if the underlying cache object is rewritten between chunk fetches, `ParallelRangeDownload` can splice bytes from two different object versions into one local cache archive.

### Finding Description
The parallel-download flow works as follows:
1. `tryPresignedParallelDownload` (commands/helpers/cache_extractor.go:257-306) issues a probe `Range: bytes=0-0` GET, capturing `ETag`, `Content-Length`, and `Last-Modified` from that single response. [1](#0-0) 
2. It then calls `downloadParallel(contentLength, date, resp.Header.Get("ETag"), ..., c.presignedRangeFetchChunk(selectedURL))`. The captured `etag` is only used for a log line in `downloadParallel`, never sent back as a conditional header. [2](#0-1) 
3. `presignedRangeFetchChunk` builds a brand-new `http.Request` per chunk with only `Range` set — no `If-Match`, no version query parameter re-validation: [3](#0-2) 
4. `ParallelRangeDownload` dispatches all chunk fetches concurrently and writes each chunk directly via `dest.WriteAt(buf, offset)`, with no cross-chunk consistency check (no shared ETag validation, no hash verification of the assembled file): [4](#0-3) 
5. The GoCloud path has the identical gap: `attrs.ETag`/`attrs.ModTime` are captured once via `Attributes()`, then `fetchChunk` calls `selectedBucket.NewRangeReader(ctx, selectedObjectName, offset, length, nil)` for every chunk with `nil` options (no `BeforeRead` conditional/version pinning): [5](#0-4) 

Because a presigned URL/object key generally refers to a *mutable* key (not an immutable version), any writer able to overwrite the object at that key while a runner job is mid-download (e.g., a concurrent job using the same cache key, or the attacker's own pipeline race-writing to the shared cache key before/while another job restores it) can cause different chunk GETs to return bytes belonging to different uploads. `ParallelRangeDownload` has no mechanism to detect or reject this — each chunk is trusted independently and blindly written at its offset, and the resulting spliced file is renamed into `c.File` and handed to the archive extractor at `commands/helpers/cache_extractor.go` `Execute` (line ~646-663) without any post-assembly integrity check (no checksum, no full-file ETag re-validation).

Neither `checkIfUpToDate`, `selectPresignedURL`, `isLocalCacheFileUpToDate`, nor the auth of the presigned URL itself protects against this — those checks only decide *which* URL/timestamp to prefer, not whether the object changed mid-transfer.

### Impact Explanation
A spliced archive combining fragments of two different cache tarballs is written to `c.File` and extracted into the job's working directory by `archive.NewExtractor(...).Extract(...)`. Depending on archive format internals, this can result in: corrupted/undefined archive parsing that copies unexpected file bytes (potential disclosure of another cache generation's content, including secrets a previous job placed under cache paths), or a crafted splice designed to make the extractor emit attacker-chosen bytes into a path that a later step trusts (cache poisoning). This matches the requested scoped impact: cache poisoning or secret-bearing file disclosure.

### Likelihood Explanation
Exploitability requires: (a) FF_USE_PARALLEL_CACHE_TRANSFER enabled with `Concurrency > 1` (opt-in feature flag, but caller-controlled per runner config, not attacker-controlled — this reduces likelihood since it's not attacker-toggleable), (b) the cache object being large enough to exceed one chunk (`chunkSize`, default 16 MiB) so multiple range requests are actually issued, and (c) the attacker being able to overwrite the same cache key's backing object while a legitimate restore is in flight — achievable by an unprivileged pipeline author who controls cache keys/timing (e.g., triggering concurrent jobs sharing a cache key, or racing an upload against a slow multi-chunk download). Race timing is nontrivial but feasible given multi-chunk downloads take measurable wall-clock time and the attacker fully controls job scheduling/retries. The precondition that this feature flag be enabled somewhat limits real-world exposure until/unless it becomes default-on.

### Recommendation
Pin all range/chunk fetches to the specific object version observed at probe time:
- For presigned S3 URLs, send `If-Match: <etag>` on every chunk `GET` in `presignedRangeFetchChunk`, and treat a `412 Precondition Failed` response as a hard error that aborts and retries the whole download (rather than silently writing mismatched bytes).
- For GoCloud, pass `&blob.ReaderOptions{...}` with driver-specific conditional/version pinning where supported (e.g., S3 `VersionId`, GCS generation), or re-check `Attributes().ETag`/`ModTime` after full assembly and reject/retry if changed.
- As a defense-in-depth measure independent of backend support, after `ParallelRangeDownload` completes, re-fetch/compare a strong content hash or re-validate `ETag` for the whole object before renaming the temp file into `c.File`, failing the download if it does not match the value captured before the chunked fetch began.

### Proof of Concept
Go unit test extending `helpers/transfer/parallel_download_test.go` / a new test in `commands/helpers/cache_extractor_test.go`:
1. Stand up an `httptest.Server` that serves two different "versions" of content for the same URL, keyed by a mutable in-memory `[]byte` slice protected by a mutex.
2. Implement the handler to honor `Range` requests as `presignedRangeFetchChunk` expects (return 206, `Content-Range`, `Last-Modified`, `ETag`).
3. Drive `tryPresignedParallelDownload`/`downloadParallel` (or directly `transfer.ParallelRangeDownload`) against this server with `chunkSize` small enough to force ≥2 chunks and `concurrency ≥ 2`.
4. Mid-download (e.g., in the handler for the second chunk request), swap the in-memory content to "version B" bytes.
5. Assert: the resulting local file at `c.File` is a byte-for-byte match to either wholly version A or wholly version B — and specifically assert it is currently **not** (i.e., demonstrate the bug) by observing the assembled file contains a chunk boundary where bytes before the swap point come from version A and bytes after come from version B, proving no `If-Match`/version pinning rejects the mixed result. [6](#0-5) [3](#0-2)

### Citations

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

**File:** commands/helpers/cache_extractor.go (L491-498)
```go
	if featureflags.IsOn(logger, os.Getenv(featureflags.UseParallelCacheTransfer)) && c.Concurrency > 1 && attrs.Size > 0 { //nolint:nestif
		if c.gocloudParallelRangeSupported(ctx, u.Scheme, selectedBucket, selectedObjectName) {
			if attrs.Size > int64(c.effectiveParallelChunkSize()) {
				fetchChunk := func(offset, length int64) (io.ReadCloser, error) {
					return selectedBucket.NewRangeReader(ctx, selectedObjectName, offset, length, nil)
				}
				return c.downloadParallel(attrs.Size, attrs.ModTime, attrs.ETag, cleanedURL, attrs.Metadata, fetchChunk)
			}
```

**File:** commands/helpers/cache_extractor.go (L524-529)
```go
	name := strings.TrimSuffix(filepath.Base(c.File), filepath.Ext(c.File))
	if etag != "" {
		logrus.WithField(logFieldHTTPETag, etag).Infoln("Downloading", name, "from", cleanedURL, "(parallel)")
	} else {
		logrus.Infoln("Downloading", name, "from", cleanedURL, "(parallel)")
	}
```

**File:** helpers/transfer/parallel_download.go (L52-79)
```go
func (w *parallelRangeWorker) downloadChunk(offset, length int64) {
	reader, err := w.fetchChunk(offset, length)
	if err != nil {
		w.recordFirstErr(err)
		return
	}
	defer func() { _ = reader.Close() }()

	chunkLen := int(length)
	if int64(chunkLen) != length {
		w.recordFirstErr(fmt.Errorf("chunk length overflows int: %d", length))
		return
	}
	buf := make([]byte, chunkLen)
	_, err = io.ReadFull(io.LimitReader(reader, length), buf)
	if err != nil {
		w.recordFirstErr(fmt.Errorf("chunk read at offset %d: %w", offset, err))
		return
	}
	n, err := w.dest.WriteAt(buf, offset)
	if err != nil {
		w.recordFirstErr(err)
		return
	}
	if int64(n) != length {
		w.recordFirstErr(fmt.Errorf("chunk write size mismatch at offset %d: wrote %d bytes, want %d", offset, n, length))
	}
}
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
