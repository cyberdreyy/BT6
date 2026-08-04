### Title
Cache freshness check trusts server `Last-Modified` timestamp instead of object identity, letting `Extract` consume a stale/mismatched local archive - (File: `commands/helpers/archive/fastzip/zip_fastzip_extractor.go`)

### Summary
`CacheExtractorCommand.Execute` in `commands/helpers/cache_extractor.go` decides whether to skip re-downloading the remote cache object purely by comparing the server-reported `Last-Modified`/`ModTime` against the local file's mtime (`isLocalCacheFileUpToDate`, `checkIfUpToDate`). If that check says "up to date," the freshly-fetched body is discarded and the pre-existing local file at `c.File` is opened and handed to `archive.NewExtractor(...).Extract(ctx)` — which for zip archives resolves to `fastzip` extractor's `Extract` in `commands/helpers/archive/fastzip/zip_fastzip_extractor.go`. There is no binding (ETag/hash/pipeline id) tying the local file to the specific object intended for the *current* job, so a stale or mismatched local file can be extracted into the live job's workspace.

### Finding Description
The reachable path is:
1. `functions/concrete/run/stages/cache_extract.go` (`CacheExtract.extract`) invokes the `cache-extractor` helper with `--file <local path>` and a presigned/GoCloud `--url`.
2. `CacheExtractorCommand.Execute` (`commands/helpers/cache_extractor.go:618-664`) calls `c.doRetry(c.download)`, which for the sequential presigned path reaches `downloadPresignedSequential` (`commands/helpers/cache_extractor.go:308-328`):
   - It performs the GET, then calls `checkIfUpToDate(c.File, resp)` (`commands/helpers/cache_extractor.go:159-162`), which only parses the `Last-Modified` header and compares it with `os.Lstat(c.File).ModTime()` via `isLocalCacheFileUpToDate` (`commands/helpers/cache_extractor.go:164-167`).
   - If `!date.After(fi.ModTime())`, the function returns `nil` immediately (`commands/helpers/cache_extractor.go:317-321`) and the already-open response body is discarded without ever calling `downloadAndSaveCache`, i.e. the freshly fetched content is thrown away and the *existing local file* is kept.
3. The same pattern is repeated in the parallel-range probe path `tryPresignedParallelDownload` (`commands/helpers/cache_extractor.go:285-291`) and the GoCloud path `handleGoCloudURL` (`commands/helpers/cache_extractor.go:482-485`), all using only a timestamp compare with no content identity check.
4. Back in `Execute`, regardless of which branch ran, `openArchive(c.File)` opens whatever file currently sits at that path and `archive.NewExtractor(format, f, size, wd)` → `fastzip.extractor.Extract(ctx)` (`commands/helpers/archive/fastzip/zip_fastzip_extractor.go:33-46`) extracts it into the job's working directory unconditionally.

Because the "up to date" decision is a pure timestamp compare and the local mtime is itself only ever set from a previously-received `Last-Modified`/`ModTime` value (via `os.Chtimes` in `downloadAndSaveCache`/`downloadParallel`, `commands/helpers/cache_extractor.go:557,601`), there is no cryptographic or identity check (no ETag/hash comparison against the object actually intended for this invocation) tying the file on disk to the object that should be restored for the current job. Any situation where:
- two job executions on the same runner share the same local cache path/key (e.g., a cache key that does not vary by ref/pipeline, or concurrent jobs racing on the same build directory), or
- the backend timestamp for a legitimately updated object is not strictly greater than a previously recorded local mtime (clock skew, object-storage timestamp preservation on copy/versioning, or an attacker who controls what gets written under a shared key)

will cause the freshness check to declare the stale local file "up to date" and route it straight into `Extract`, restoring content that does not belong to the live job's actual cache object. Nothing downstream (path validation, overwrite guards) re-validates that the extracted archive corresponds to the currently requested remote object — the check is bypassed entirely once the timestamp test short-circuits.

### Impact Explanation
A job can end up extracting cache content that was written by a different job/pipeline execution (including attacker-controlled content from an earlier run using the same key/path) instead of the object that is actually current for the live job. This is cross-job restore confusion / state poisoning scoped to cache extraction: build artifacts, dependency directories, or other cache paths in the victim job's workspace get populated from stale/attacker-influenced data rather than the intended remote object, without any error or warning ("is up to date" is logged as success).

### Likelihood Explanation
Exploitability depends on being able to make the server-reported timestamp fail to exceed the local file's recorded mtime while the underlying object content differs from what the local file holds. This is most plausible in shared/concurrent-runner scenarios where multiple job executions target the same local cache file path with a cache key that does not disambiguate by pipeline/job (a scenario within a normal, unprivileged pipeline author's control via `cache:key` configuration), or under clock-skew/backend-timestamp edge cases. It does not require any admin/privileged action — only ordinary CI configuration and job scheduling/timing that an unprivileged pipeline author can influence.

### Recommendation
Bind the "up to date" decision to object identity, not just time: require an ETag/hash comparison (already fetched into `resp.Header.Get("ETag")`/`attrs.ETag` but currently unused for the skip decision) recorded in the cache metadata sidecar and compared before skipping the download, and fail closed (re-download) if the recorded identity is missing or stale contexts cannot be disambiguated (e.g., always re-download when concurrency/multiple in-flight jobs share the target path).

### Proof of Concept
Go test plan for `commands/helpers/cache_extractor_test.go`:
1. Seed `c.File` locally with archive content "A" and set its mtime to `T`.
2. Start an HTTP test server that returns archive content "B" (different, attacker-controlled) with `Last-Modified: T` (not after the local mtime).
3. Run `CacheExtractorCommand.Execute` with `URL` pointing at the test server and `File` pointing at the seeded local file.
4. Assert that after execution, the extracted contents in the working directory match archive "A" (stale, wrong) rather than "B" (the object the live job actually requested), demonstrating that `Extract` consumed the stale local residue instead of the current remote object — and additionally assert that no error/warning distinguishes this mismatch from a legitimate up-to-date short-circuit. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** commands/helpers/cache_extractor.go (L159-167)
```go
func checkIfUpToDate(path string, resp *http.Response) (bool, time.Time) {
	date, _ := time.Parse(http.TimeFormat, resp.Header.Get("Last-Modified"))
	return isLocalCacheFileUpToDate(path, date), date
}

func isLocalCacheFileUpToDate(path string, date time.Time) bool {
	fi, _ := os.Lstat(path)
	return fi != nil && !date.After(fi.ModTime())
}
```

**File:** commands/helpers/cache_extractor.go (L285-291)
```go
	date, _ := time.Parse(http.TimeFormat, resp.Header.Get("Last-Modified"))
	if isLocalCacheFileUpToDate(c.File, date) {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, transfer.RangeProbeBodyMaxDiscard))
		_ = resp.Body.Close()
		logrus.Infoln(filepath.Base(c.File), "is up to date")
		return true, nil
	}
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

**File:** commands/helpers/cache_extractor.go (L482-485)
```go
	if isLocalCacheFileUpToDate(c.File, attrs.ModTime) {
		logrus.Infoln(filepath.Base(c.File), "is up to date")
		return nil
	}
```

**File:** commands/helpers/cache_extractor.go (L618-664)
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
