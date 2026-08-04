Confirmed: `presignedRangeFetchChunk` at [1](#0-0)  issues a plain `GET` with a `Range` header and no `If-Match`/`If-Unmodified-Since` conditional headers pinning it to the ETag/`Last-Modified` value already captured from the probe response at line 304. Likewise the GoCloud path calls `selectedBucket.NewRangeReader(ctx, selectedObjectName, offset, length, nil)` at [2](#0-1)  with a `nil` options struct, so no generation/version constraint is passed to the blob backend either. `ParallelRangeDownload` itself just spins up N independent chunk fetches and writes each at its offset via `WriteAt`, with no cross-chunk consistency check [3](#0-2) .

### Title
Parallel/chunked cache and artifact restore can silently splice bytes from two different backing object versions - (File: commands/helpers/cache_extractor.go, helpers/transfer/parallel_download.go, network/gitlab.go)

### Summary
When `FF_USE_PARALLEL_CACHE_TRANSFER` or `FF_USE_PARALLEL_ARTIFACT_TRANSFER` is enabled, cache/artifact restore fetches multiple byte ranges of the same object concurrently and reassembles them by offset, but no chunk fetch is bound to the object's ETag/generation captured at probe time. If the backing object storage entry is overwritten between chunk fetches of a single download attempt, the reassembled local file contains bytes from two different object versions before it is handed to the zip/tar `Extract` code.

### Finding Description
`tryPresignedParallelDownload` reads `ETag`/`Last-Modified` once from an initial `Range: bytes=0-0` probe [4](#0-3) , then calls `downloadParallel` with `c.presignedRangeFetchChunk(selectedURL)` [5](#0-4) . Each subsequent per-chunk request built by `presignedRangeFetchChunk` is a bare `GET` with only a `Range` header - it never sets `If-Match: <etag>` or `If-Unmodified-Since` [6](#0-5) . The GoCloud path is equally unconstrained: `fetchChunk` calls `selectedBucket.NewRangeReader(ctx, selectedObjectName, offset, length, nil)` with `nil` options (no generation/version pin) [2](#0-1) . `transfer.ParallelRangeDownload` fires all chunk requests concurrently via independent goroutines and writes each chunk to its byte offset with `WriteAt`, only checking per-chunk I/O errors/length, never a whole-file digest or version consistency [3](#0-2) . `downloadParallel` then renames the assembled temp file directly onto `c.File` as soon as `writer.Close()` succeeds, with no post-assembly hash/CRC verification against the object's reported ETag [7](#0-6) . The same unconditional-Range pattern exists for artifact direct-download parallelism in `tryArtifactParallelDownload`'s `fetchChunk` closure [8](#0-7) .

If the object behind the presigned URL / GoCloud object name is replaced between the time chunk A and chunk B are fetched (e.g., a concurrent job/pipeline in the same project re-uploads a cache to the same key, or races an artifact overwrite window), the assembled file on disk contains region A from version 1 and region B from version 2. That file is then passed unmodified into `archive.NewExtractor(...).Extract(ctx)`, which for zip archives resolves to `fastzip.Extractor.Extract` in `commands/helpers/archive/fastzip/zip_fastzip_extractor.go` [9](#0-8) .

However, the actual scoped impact ("mixed output tree" via `Extract`) is significantly undercut by two facts:
1. Zip format integrity: `fastzip`/`archive/zip` reads a single end-of-central-directory record and per-entry CRC32 values. Splicing byte ranges from two structurally different archive versions almost always corrupts the central directory offsets or fails per-entry CRC32 verification during decompression, causing `Extract` to return an error rather than silently producing a "trusted" mixed tree. A genuinely silent, semantically-coherent splice requires the attacker to control both object versions byte-for-byte at chunk-aligned boundaries (same total length, same central directory layout, same entry offsets) — an extremely narrow, self-inflicted condition since the attacker would need write access to the exact same cache/artifact object concurrently with the victim's download, and craft the two versions to still parse as a valid archive post-splice.
2. No download-attempt integrity check exists at all (whole-file digest against ETag), so this is a genuine gap, but it functions as a data race / corrupt-download resilience gap rather than a reliably exploitable "attacker chooses the resulting tree" primitive, since realistic archive formats will fail closed (extraction error) rather than fail open (mixed but valid tree) in the overwhelming majority of cases.

### Impact Explanation
Concrete impact is denial-of-service / job failure via corrupted extraction (checksum/central-directory errors) in the common case. A crafted, deterministic "mixed but valid and attacker-influenced tree" requires the attacker to control the exact byte layout of both object versions such that a partial splice still parses as a valid, coherent archive — this is a much narrower and harder precondition than the question implies, and is not demonstrated to be practically achievable against `fastzip`/`archive/zip`'s CRC and central-directory validation.

### Likelihood Explanation
Requires: (a) `FF_USE_PARALLEL_CACHE_TRANSFER` or `FF_USE_PARALLEL_ARTIFACT_TRANSFER` enabled, (b) an object larger than one chunk, (c) the ability to overwrite the exact same cache key/artifact object mid-download of a victim job, and (d) crafting two archive versions whose spliced combination still parses cleanly. (a)-(c) are plausible for a pipeline author racing their own project's shared cache key; (d) is the binding constraint that makes a reliable, silent "mixed but valid" exploit unlikely in practice, though a DoS via corrupted extraction is easily achievable.

### Recommendation
Bind every chunk request in a single `downloadParallel`/parallel-artifact-download attempt to the object version captured at probe time: send `If-Match: <etag>` (or generation-pinned reader options for GoCloud buckets that support it) on each range request, and fail/retry the whole attempt if any chunk response indicates the precondition no longer matches (`412 Precondition Failed`) or if a probed ETag differs from a chunk's response ETag. Additionally, verify the fully assembled file's digest/ETag before renaming it onto `c.File`, rather than relying solely on per-chunk length checks in `transfer.ParallelRangeDownload`.

### Proof of Concept
Go integration test in `helpers/transfer/parallel_download_test.go` style: implement a `fetchChunk` stub that returns bytes from `"versionA"` for the first N chunks and switches to `"versionB"` bytes for subsequent chunks (simulating the object changing mid-download, as in `TestParallelRangeDownload_WriteAt`). Assert that `ParallelRangeDownload` succeeds (no error) yet the resulting file is a byte-for-byte splice of both versions — proving the missing consistency guard. Then, in `commands/helpers/cache_extractor_test.go`, build two structurally different zip payloads of the same total chunked-length, serve version A for early ranges and version B for later ranges via `httptest.Server`, run `CacheExtractorCommand.Execute` with `FF_USE_PARALLEL_CACHE_TRANSFER=true`, and assert that either (a) extraction fails with a checksum/format error (showing fail-closed behavior, downgrading severity to DoS), or (b) — if achievable — the extracted tree contains a mix of entries from both payloads with no error (confirming the stronger claim).

### Citations

**File:** commands/helpers/cache_extractor.go (L279-291)
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
```

**File:** commands/helpers/cache_extractor.go (L303-305)
```go
	cleanedURL := url_helpers.CleanURL(selectedURL)
	err = c.downloadParallel(contentLength, date, resp.Header.Get("ETag"), cleanedURL, headersToCacheMetadata(resp.Header), c.presignedRangeFetchChunk(selectedURL))
	return true, err
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

**File:** commands/helpers/cache_extractor.go (L493-497)
```go
			if attrs.Size > int64(c.effectiveParallelChunkSize()) {
				fetchChunk := func(offset, length int64) (io.ReadCloser, error) {
					return selectedBucket.NewRangeReader(ctx, selectedObjectName, offset, length, nil)
				}
				return c.downloadParallel(attrs.Size, attrs.ModTime, attrs.ETag, cleanedURL, attrs.Metadata, fetchChunk)
```

**File:** commands/helpers/cache_extractor.go (L547-563)
```go
	err = transfer.ParallelRangeDownload(contentLength, chunkSize, concurrency, destAt, fetchChunk)
	if err != nil {
		_ = writer.Close()
		return retryableErr{err: err}
	}

	if err := writer.Close(); err != nil {
		return err
	}
	// file is closed by writer.Close(); do not call file.Close()
	if err := os.Chtimes(tmpName, time.Now(), modTime); err != nil {
		return err
	}
	if err := os.Rename(tmpName, c.File); err != nil {
		return fmt.Errorf("renaming: %w", err)
	}
	return writeCacheMetadataFile(c.File, metadata)
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

**File:** network/gitlab.go (L1209-1220)
```go
	fetchChunk := func(offset, length int64) (io.ReadCloser, error) {
		rangeHeaders := http.Header{"Range": []string{fmt.Sprintf("bytes=%d-%d", offset, offset+length-1)}}
		resp, err := cli.do(ctx, locationURL, http.MethodGet, nil, "", rangeHeaders)
		if err != nil {
			return nil, err
		}
		if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusPartialContent {
			_ = resp.Body.Close()
			return nil, fmt.Errorf("range request failed: %s", resp.Status)
		}
		return resp.Body, nil
	}
```

**File:** commands/helpers/archive/fastzip/zip_fastzip_extractor.go (L33-46)
```go
func (e *extractor) Extract(ctx context.Context) error {
	opts, err := getExtractorOptionsFromEnvironment()
	if err != nil {
		return err
	}

	extractor, err := fastzip.NewExtractorFromReader(e.r, e.size, e.dir, opts...)
	if err != nil {
		return err
	}
	defer extractor.Close()

	return extractor.Extract(ctx)
}
```
