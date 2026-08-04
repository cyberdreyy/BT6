### Title
Unbounded decompression in tar+zstd Extract() enables disk-exhaustion zip-bomb DoS - (File: commands/helpers/archive/tarzstd/tarzstd_extractor.go)

### Summary
`extractor.Extract` copies each regular file's decompressed content with `io.Copy(f, tr)` at line 93 with no output-size cap and no context-aware cancellation during the copy, and `zstd.WithDecoderLowmem(true)` at line 34 only bounds decoder memory, not decompressed output size. A crafted cache/artifact archive with one tar header declaring an enormous `Size` and a small zstd stream that decompresses to a huge amount of data can fill the shared disk before extraction can be aborted.

### Finding Description
`Extract` reads tar headers in a loop and only checks `ctx.Err()` once per header, at line 70, before deciding how to handle the entry. Once execution reaches the `fi.Mode().IsRegular()` branch, `io.Copy(f, tr)` at line 93 runs to completion with no intermediate context check and no limit on the number of bytes written. `tar.Reader` will allow reads up to the attacker-controlled `hdr.Size` field from the underlying `zstd.Decoder`, so a single tar entry with an astronomically large declared `Size` backed by a classic decompression-bomb zstd stream (small compressed payload, huge decompressed output) causes `io.Copy` to keep writing to disk until the stream truly ends, the declared size is reached, or a disk-full write error occurs [1](#0-0) . There is no check anywhere in this function—or in its callers—that bounds decompressed/extracted size; `MaxUploadedArchiveSize` only limits the *compressed archive upload* size in `cache_archiver.go`, not the size accepted during extraction [2](#0-1) . Both real callers, `cache_extractor.go`'s `Execute` and `artifacts_downloader.go`'s `Execute`, invoke `extractor.Extract(context.Background())`, a context that is never cancelled, so the `ctx.Err()` check at line 70 is dead code on these paths and provides no real defense even between headers [3](#0-2) [4](#0-3) . Cache extraction is reachable from ordinary job pipelines via the `cache-extractor` helper invoked by the shell/job runtime with attacker-controlled cache contents (job scripts fully control what gets archived and, via `Shared`/cross-project cache keys, what gets fetched by other jobs on the same runner host) [5](#0-4) .

### Impact Explanation
On a shared runner/cache host, a single crafted cache or artifact archive can drive unbounded disk writes during extraction, exhausting shared disk space used by concurrently running or subsequently scheduled jobs (build directories, other caches, docker/image layers, logs), causing job failures unrelated to the attacker's own job — a persistent multi-tenant denial-of-service against the shared helper/cache storage.

### Likelihood Explanation
Feasible and repeatable: the attacker only needs the ability to produce a cache/artifact archive (any GitLab user with a pipeline) and does not need special executor privileges. Constructing a small zstd stream that decompresses to gigabytes/terabytes of data (a "zstd bomb") is straightforward using standard zstd tooling, and no size-quota or streaming write-limit exists in the extraction path to stop it.

### Recommendation
Enforce a maximum decompressed/extracted size (per-file and/or cumulative) while streaming in `Extract`, e.g. wrap `tr` in a limited reader per entry (`io.CopyN` with an explicit cap, or track cumulative bytes written and abort once a configurable quota such as a `MaxExtractedSize`/`CACHE_MAX_EXTRACTED_SIZE` is exceeded), and check `ctx.Err()` periodically inside the copy loop (e.g., copy in bounded chunks and check ctx between chunks) so a cancellable context can actually interrupt a large single-file copy. Apply the same fix to the zipzstd extractor if it has an analogous unlimited copy.

### Proof of Concept
Go test outline in `commands/helpers/archive/tarzstd`:
1. Build a tar+zstd stream containing one header with `Name: "bomb"`, `Size: 1<<40` (or similarly huge value), followed by a real zstd-compressed payload that decompresses to a very large but test-feasible size (e.g., several GB) by repeating a highly compressible pattern.
2. Call `extractor.Extract(ctx)` with a `context.WithTimeout` or manually cancelled context, and assert that either (a) extraction aborts promptly near the timeout (bounded runtime) rather than running until the fake huge size or real disk limits are hit, or (b) extraction stops once a defined byte quota is exceeded, returning an error instead of continuing `io.Copy`.
3. As a negative-control baseline (current code), demonstrate the copy loop runs to completion (or disk fills) regardless of an already-cancelled context passed at call time, proving the vulnerability: `ctx, cancel := context.WithCancel(...); cancel(); err := extractor.Extract(ctx)` should ideally return `context.Canceled` promptly but instead only checks `ctx.Err()` between tar headers, so a single huge entry proceeds unabated.

### Citations

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L87-99)
```go
		case fi.Mode().IsRegular():
			f, err := os.Create(path)
			if err != nil {
				return err
			}

			if _, err := io.Copy(f, tr); err != nil {
				f.Close()
				return err
			}
			if err := f.Close(); err != nil {
				return err
			}
```

**File:** commands/helpers/cache_archiver.go (L445-457)
```go
func (c *CacheArchiverCommand) uploadArchiveIfNeeded(size int64) {
	if c.URL == "" && c.GoCloudURL == "" {
		logrus.Infoln(
			"No URL provided, cache will not be uploaded to shared cache server. " +
				"Cache will be stored only locally.")
		return
	}

	if c.MaxUploadedArchiveSize != 0 && size > c.MaxUploadedArchiveSize {
		logrus.Infoln(fmt.Sprintf("Cache archive size (%d) is too big (Limit is set to %d). "+
			"Cache will be stored only locally.", size, c.MaxUploadedArchiveSize))
		return
	}
```

**File:** commands/helpers/cache_extractor.go (L660-663)
```go
	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
	}
```

**File:** commands/helpers/artifacts_downloader.go (L131-140)
```go
	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	// Extract artifacts file
	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
	}
```

**File:** functions/concrete/run/stages/cache_extract.go (L81-88)
```go
func (s CacheExtract) extract(ctx context.Context, e *env.Env, src CacheSource) error {
	archiveFile := s.archivePath(e, src.Key)

	args := []string{
		"cache-extractor",
		"--file", archiveFile,
		"--timeout", strconv.Itoa(s.Timeout),
	}
```
