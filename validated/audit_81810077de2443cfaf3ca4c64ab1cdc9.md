### Title
`tryPresignedParallelDownload`/`downloadParallel` splice bytes from different cache object versions across parallel range fetches - (File: `commands/helpers/cache_extractor.go`)

### Summary
`tryPresignedParallelDownload` selects a presigned cache URL, probes it once with `Range: bytes=0-0` to learn size/ETag/Last-Modified, then hands the URL to `downloadParallel`, which fans out N independent `GET` requests (one per chunk) via `presignedRangeFetchChunk` and writes each chunk directly to its byte offset with `WriteAt`. None of these per-chunk requests are pinned to the object version observed in the probe (no `If-Match`/ETag/version-id constraint), so if the backing object is overwritten between/during the chunk fetches, the resulting local file is assembled from bytes belonging to two different uploads.

### Finding Description
The probe request captures `resp.Header.Get("ETag")` at [1](#0-0) , but that ETag is only ever used for a log line in `downloadParallel` [2](#0-1)  — it is never sent back as a conditional header on the chunk requests. Each chunk fetch is a brand-new, unauthenticated-of-version `http.Request` built in `presignedRangeFetchChunk`, with only a `Range` header and no `If-Match`: [3](#0-2) . The actual concurrent fetch/write loop in `transfer.ParallelRangeDownload` calls `fetchChunk(offset, length)` independently for every chunk and writes results with `dest.WriteAt(buf, offset)`, with no cross-chunk version check at all: [4](#0-3) . The same pattern exists on the GoCloud path, where `fetchChunk` calls `selectedBucket.NewRangeReader(ctx, selectedObjectName, offset, length, nil)` per chunk with no generation/version pin: [5](#0-4) .

By contrast, the sequential fallback path (`downloadPresignedSequential`/`downloadAndSaveCache`) streams the entire object from a single HTTP response body, so it is inherently consistent — there is exactly one version bound to one connection. The parallel path breaks this invariant by turning one logical "download the cache object" operation into N independent HTTP requests that a remote object store (e.g., S3) does not guarantee to serve from the same object version, especially if the object is overwritten mid-download.

An attacker path: GitLab cache keys (including fallback keys) are attacker/pipeline-author controlled via `.gitlab-ci.yml`, and caches for a given key can be shared across jobs/pipelines/branches within the same project (e.g., feature-branch fallback to a shared/default-branch cache key). A pipeline author with cache-write access can race a cache-upload job to overwrite the shared cache object at the exact moment another job (their own or another user/pipeline sharing that key) is mid-parallel-download. Because the chunk requests are unpinned, some chunks land from the "before" object and some from the "after" object, producing a spliced local archive that is neither of the two legitimately-uploaded versions. Existing safeguards (`isLocalCacheFileUpToDate`, ETag logging, `Chtimes`) only compare timestamps/mtimes for skip-if-up-to-date decisions taken from the single probe response — none of them re-validate that every chunk actually came from that same version before it's written and renamed into `c.File` and extracted.

### Impact Explanation
The spliced archive is trusted and passed straight to `archive.NewExtractor(...).Extract(...)` in `Execute` [6](#0-5) . If an attacker can align two cache-archive versions they control (both self-uploaded, since cache overwrite requires write access to that cache key) so that the differing region falls entirely within one chunk boundary, they can get the runner to extract a file whose content diverges from what any single upload actually contained. Where cache keys are shared across users/branches (fallback keys, protected-branch cache reuse) this becomes a targeted cache-poisoning vector against a victim job that only intended to consume one specific cache version — the victim's job extracts attacker-chosen bytes it never legitimately uploaded or downloaded as a whole. Best case for the defender, the corruption breaks archive integrity checks (CRC) and just fails the job (DoS); worst case, a sufficiently crafted pair of archives yields silent content substitution in the extracted cache.

### Likelihood Explanation
Requires: (1) `FF_USE_PARALLEL_CACHE_TRANSFER` enabled with `Concurrency > 1` (an admin/runner config choice, not attacker-controlled, which reduces default exposure), (2) a cache object large enough to exceed one chunk (`>16MiB` by default) to enter the parallel path, and (3) precise timing to overwrite the object mid-download and control over both archive versions to make the splice land on a chunk boundary that produces a still-parseable, still-useful poisoned artifact. This is a genuine TOCTOU/logic gap (missing per-chunk version pinning) rather than a theoretical concern, but exploiting it for anything beyond archive corruption (DoS) requires non-trivial crafting and race-winning, so real-world exploitation is feasible but not trivial.

### Recommendation
Pin every chunk request to the exact object version observed in the initial probe:
- For presigned S3-style URLs, send `If-Match: <etag>` on every `presignedRangeFetchChunk` request and treat a `412 Precondition Failed` as a hard failure (abort and retry the whole download), rather than silently accepting whatever bytes come back.
- For GoCloud buckets, pass `*blob.ReaderOptions` (or provider-specific version/generation constraints, e.g. S3 `VersionId`, GCS `Generation`) to `NewRangeReader` so every chunk read is bound to the same immutable object version fetched during `Attributes`.
- As a defense-in-depth measure, verify the ETag returned on each chunk response (when present) matches the ETag captured during the initial probe before writing the chunk via `WriteAt`, and fail the whole parallel download on mismatch instead of assembling mixed-version output.

### Proof of Concept
Go unit test extending `helpers/transfer/parallel_download_test.go` style tests, targeting `transfer.ParallelRangeDownload` (and by extension `downloadParallel`):
```go
func TestParallelRangeDownload_MixedVersionsAreNotDetected(t *testing.T) {
    versionA := bytes.Repeat([]byte("A"), 20)
    versionB := bytes.Repeat([]byte("B"), 20)

    var calls int32
    fetchChunk := func(offset, length int64) (io.ReadCloser, error) {
        n := atomic.AddInt32(&calls, 1)
        // Simulate the object being overwritten mid-download: first chunk
        // request sees version A, subsequent ones see version B.
        src := versionA
        if n > 1 {
            src = versionB
        }
        return io.NopCloser(bytes.NewReader(src[offset : offset+length])), nil
    }

    f, _ := os.CreateTemp(t.TempDir(), "poc")
    err := ParallelRangeDownload(20, 7, 4, f, fetchChunk)
    require.NoError(t, err) // no error is raised despite mixed content

    got, _ := os.ReadFile(f.Name())
    // Assert the output is neither versionA nor versionB in full -> proves splicing.
    assert.NotEqual(t, versionA, got)
    assert.NotEqual(t, versionB, got)
}
```
Expected assertion: the function returns `nil` error while producing an output file that matches neither source version, proving that `ParallelRangeDownload` (and therefore `tryPresignedParallelDownload`/`downloadParallel`) has no mechanism to detect or reject cross-version chunk mixing.

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

**File:** commands/helpers/cache_extractor.go (L493-498)
```go
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

**File:** commands/helpers/cache_extractor.go (L646-663)
```go
	f, size, format, err := openArchive(c.File)
	if os.IsNotExist(err) {
		warningln("Cache file does not exist")
	}
	if err != nil {
		logrus.Fatalln(err)
	}
	defer f.Close()

	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
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
