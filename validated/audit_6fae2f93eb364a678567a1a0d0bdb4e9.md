### Title
`downloadParallel` fetches range chunks without pinning them to the probed object version, allowing cross-version splicing of cache archives - (File: commands/helpers/cache_extractor.go)

### Summary
`downloadParallel` (commands/helpers/cache_extractor.go:513-564) writes concurrently fetched byte ranges directly to disk via `transfer.ParallelRangeDownload`, but neither the presigned-URL chunk fetcher nor the GoCloud chunk fetcher attaches any conditional/version-pinning parameter (e.g. `If-Match: <etag>`, S3 `versionId`, GCS generation) to the per-chunk requests. The ETag/Last-Modified captured from the initial probe is used only for logging and metadata bookkeeping, never for validating that every chunk actually came from that same object instance.

### Finding Description
Two code paths feed `fetchChunk` into `downloadParallel`:

- `presignedRangeFetchChunk` (commands/helpers/cache_extractor.go:337-354) issues a plain `http.NewRequest(GET, rawURL, ...)` with only a `Range` header per chunk — no `If-Match`/`If-Unmodified-Since` header is set using the ETag/Last-Modified obtained from the initial probe request in `tryPresignedParallelDownload` (cache_extractor.go:257-306). [1](#0-0) 
- The GoCloud path's `fetchChunk` closure calls `selectedBucket.NewRangeReader(ctx, selectedObjectName, offset, length, nil)` with a `nil` options argument, again with no generation/version pinning derived from the `attrs.ETag`/`attrs.ModTime` captured earlier. [2](#0-1) 

`transfer.ParallelRangeDownload` (helpers/transfer/parallel_download.go:91-113) then dispatches these per-chunk fetches concurrently via goroutines, writing each chunk's bytes directly at its byte offset with `dest.WriteAt` — there is no comparison of ETags/Last-Modified/version IDs returned by individual chunk responses, and no rollback if a later chunk's response headers differ from the first. [3](#0-2) 

Because a presigned URL or a GoCloud object name is bound to a mutable cache key (not to an immutable object version), if the underlying object is overwritten between the initial probe/`Attributes()` call and the completion of all chunk fetches (or between individual chunk fetches themselves), `downloadParallel` will happily splice bytes from two different object states into a single local archive file at `c.File`. The only downstream validation is `os.Chtimes`/`os.Rename` and `writeCacheMetadataFile`, none of which detect content mixing — `writeCacheMetadataFile` just records the *first* probe's ETag as metadata without ever comparing it to what was actually written. [4](#0-3) 

Attacker path: an unprivileged pipeline author controls the cache key (and thus the object name/prefix used for GoCloud/presigned URLs) via `.gitlab-ci.yml`'s `cache:key`, `cache:key:files`, and the fallback-key mechanism. Since a cache object at a given key can be freely re-uploaded by any job that runs with write access to that cache scope (e.g. a job on the same branch or a job manipulating `cache:key`/fallback keys to collide with another job's cache scope), the attacker can arrange for two archives (e.g. one benign, one with a path-traversal/malicious symlink entry crafted for the later extraction step) to be served during a single parallel download by uploading a new object mid-download or by racing with retry/backoff timing that `downloadParallel`'s own retry loop provides. The resulting spliced local file is then handed unmodified to `archive.NewExtractor`/`Extract()` in `Execute` (cache_extractor.go:646-663), which will attempt to parse whatever bytes ended up on disk — meaning corrupted/spliced archive headers or offsets from mismatched versions can produce unpredictable extraction behavior (corruption, or, if archive format offsets are trusted, misparsed entries).

Existing checks do not prevent this: `isLocalCacheFileUpping` and the up-to-date short-circuit only compare against the local file's own mtime once at the start, not per-chunk; the ETag/Last-Modified values captured up front are logged/stored but never enforced as a consistency invariant across the whole parallel transfer.

### Impact Explanation
Successful exploitation produces a locally-reconstructed cache archive built from bytes belonging to two distinct object versions. Depending on where the version switch lands relative to archive structure (e.g., zip central directory vs. local file headers), this can cause: (a) corrupted-but-parseable archives with attacker-controlled bytes ending up written to the job's working directory during extraction (cache poisoning), or (b) extraction of unintended files/content that were not part of either individually-uploaded archive, potentially leading to secret-bearing file disclosure or planting of malicious files in the build directory if the spliced result parses as a valid archive with attacker-chosen entries. This matches the "cache poisoning or secret-bearing file disclosure" impact class referenced in the question.

### Likelihood Explanation
Preconditions: FF_USE_PARALLEL_CACHE_TRANSFER must be enabled and Concurrency > 1, and the object must exceed the parallel chunk-size threshold — this is an opt-in feature flag, which reduces default exposure but is fully attacker-triggerable once enabled by the runner operator (a common tuning choice for performance). Given the flag is on, the attacker needs write access to the cache backend key used by the job (normal capability for any pipeline author on their own project/branch cache scope, or via fallback-key collisions across branches within the same cache path), and needs to time an overwrite of the cache object between the initial probe and completion of the parallel GETs. This requires winning a race window that is proportional to the download duration (potentially widened by controlling job/archive size, chunk size, and network latency), making it feasible but non-trivial to reliably win. A Go unit test can deterministically demonstrate the missing invariant without needing to win a real network race, since `fetchChunk` is a plain function pointer with no version binding.

### Recommendation
- In `presignedRangeFetchChunk`, set `If-Match: <etag>` (or `If-Unmodified-Since: <lastModified>`) on every chunk request using the ETag/Last-Modified captured during the initial probe, and treat a non-matching response (412 Precondition Failed, or a differing `ETag`/`Last-Modified` in the chunk response) as a hard download failure that aborts the whole parallel transfer.
- In the GoCloud path, pass version/generation-pinning `ReaderOptions` (when the backend supports it, e.g., S3 `versionId`, GCS `generation`) captured from the initial `Attributes()` call, or explicitly verify each range reader's reported ETag/generation against the originally captured value before writing to `WriteAt`.
- In `transfer.ParallelRangeDownload`/`parallelRangeWorker.downloadChunk`, add an optional per-chunk version-check callback so mismatches abort the whole job and never partially populate the destination file that gets renamed into place.

### Proof of Concept
Go unit test in `helpers/transfer` demonstrating the missing invariant (no real network needed):

```go
func TestParallelRangeDownload_NoVersionPinning_AllowsSplicing(t *testing.T) {
    const total = int64(20)
    versionA := bytes.Repeat([]byte("A"), int(total))
    versionB := bytes.Repeat([]byte("B"), int(total))

    var switched int32
    fetchChunk := func(offset, length int64) (io.ReadCloser, error) {
        // Simulate the object being overwritten mid-download: first chunk uses
        // version A, subsequent chunks silently use version B. No ETag/If-Match
        // check is passed or enforced by ParallelRangeDownload.
        if atomic.AddInt32(&switched, 1) == 1 {
            return io.NopCloser(bytes.NewReader(versionA[offset : offset+length])), nil
        }
        return io.NopCloser(bytes.NewReader(versionB[offset : offset+length])), nil
    }

    f, _ := os.CreateTemp(t.TempDir(), "spliced")
    err := ParallelRangeDownload(total, 5, 4, f, fetchChunk)
    require.NoError(t, err) // succeeds despite mixing versions

    got, _ := os.ReadFile(f.Name())
    // Assert the bug: output contains bytes from BOTH versions A and B,
    // i.e. it is neither versionA nor versionB in full.
    assert.NotEqual(t, versionA, got)
    assert.NotEqual(t, versionB, got)
    assert.Contains(t, string(got), "A")
    assert.Contains(t, string(got), "B")
}
```

This confirms `ParallelRangeDownload` (and by extension `downloadParallel`, which calls it with fetchers that carry no version-pinning headers) has no invariant preventing a spliced result when the backing object changes mid-transfer, satisfying "all restored cache bytes must come from one bound object version" as violated.

### Citations

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

**File:** commands/helpers/cache_extractor.go (L492-498)
```go
		if c.gocloudParallelRangeSupported(ctx, u.Scheme, selectedBucket, selectedObjectName) {
			if attrs.Size > int64(c.effectiveParallelChunkSize()) {
				fetchChunk := func(offset, length int64) (io.ReadCloser, error) {
					return selectedBucket.NewRangeReader(ctx, selectedObjectName, offset, length, nil)
				}
				return c.downloadParallel(attrs.Size, attrs.ModTime, attrs.ETag, cleanedURL, attrs.Metadata, fetchChunk)
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

**File:** commands/helpers/cache_metadata.go (L18-38)
```go
// writeCacheMetadataFile dumps a file alongside the archive, holding all metadata. Before writing, the metadata keys
// are normalized with [normalizeMetadataKey].
func writeCacheMetadataFile(archiveFilePath string, metadata map[string]string) error {
	normalized := map[string]string{}
	for k, v := range metadata {
		if k == "" {
			continue
		}
		normalized[normalizeCacheMetadataKey(k)] = v
	}

	// json.Marshal won't ever fail for map[string]string
	data, _ := json.Marshal(normalized)

	file := filepath.Join(filepath.Dir(archiveFilePath), cacheMetadataFileName)
	if err := os.WriteFile(file, data, 0640); err != nil {
		return fmt.Errorf("writing metadata file: %w", err)
	}

	return nil
}
```
