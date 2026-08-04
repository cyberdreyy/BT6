Confirmed: `extractZipFileEntry` in `helpers/archives/zip_extract.go` performs an unbounded `io.Copy(out, in)` from a zip entry's decompressor to disk, with no decompressed-size ceiling anywhere in the call chain (`ExtractZipFile` → `ExtractZipArchive` → `extractZipFile` → `extractZipFileEntry`). The only size guard found in the codebase, `MaxUploadedArchiveSize` (in `cache/cacheconfig/cacheconfig.go`, `commands/helpers/cache_archiver.go`), only limits the *compressed* archive being *uploaded* to cache storage — it does not apply to artifact downloads/extraction or to decompressed output size.

### Title
Unbounded zip entry decompression enables disk-exhaustion DoS during extraction - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipFileEntry` streams a zip entry's decompressed bytes directly to disk via `io.Copy(out, in)` with no cap on the number of bytes written. An unprivileged pipeline author can craft or provide a cache/artifact zip with a small compressed footprint but an enormous decompressed payload (a "zip bomb"), causing the runner host to exhaust disk space during `ExtractZipFile`/`ExtractZipArchive` execution.

### Finding Description
The extraction path is: `ExtractZipFile` (`helpers/archives/zip_extract.go:112-120`) opens the archive and calls `ExtractZipArchive` (`:85-110`), which iterates `archive.File` and calls `extractZipFile` (`:61-83`) for each entry, dispatching regular files to `extractZipFileEntry` (`:41-59`). That function opens the compressed entry reader (`file.Open()`) and copies it to disk with `io.Copy(out, in)` at line 56 — no `io.LimitReader`, no running total, no comparison against any configured maximum. This code is reached from both artifact extraction (`commands/helpers/artifacts_downloader.go` via `archive.NewExtractor`/`Extract`) and cache extraction (`commands/helpers/cache_extractor.go`), both of which are driven by job-controlled artifact/cache contents — i.e., content an unprivileged pipeline author fully controls (their own job's artifacts, or a cache archive they push then later pull, subject to cache key/scoping). The only size enforcement found repo-wide, `MaxUploadedArchiveSize` in `cache/cacheconfig/cacheconfig.go` and `commands/helpers/cache_archiver.go`, guards the *compressed upload* size and is irrelevant to decompression; nothing in the extraction path checks cumulative decompressed bytes, per-file bytes, or available disk space before or during `io.Copy`.

### Impact Explanation
On a shared runner host (e.g., shell executor or any executor sharing a filesystem/volume across concurrent jobs), a job that downloads/extracts a crafted zip artifact or cache can drive `io.Copy` to write far more data than the compressed size suggests, filling the shared disk/volume/inode table. This can cause other concurrently running jobs on the same host to fail (out-of-space errors, inability to write logs/cache/artifacts), constituting persistent multi-tenant disruption until disk space is manually reclaimed.

### Likelihood Explanation
Feasible and repeatable by any pipeline author who can define `artifacts:` or `cache:` in their `.gitlab-ci.yml` (or otherwise supply a `.zip` file to be extracted by the runner). Standard DEFLATE zip bombs (nested or single-stream, achieving compression ratios of 1000:1 or far higher) are well-known and trivially reproducible; the attacker needs no special runner privileges, only the ability to have their job's artifact/cache be downloaded and extracted by the runner in a later stage or by a dependent job — a normal, supported workflow.

### Recommendation
Enforce a decompressed-size ceiling while streaming: wrap the per-entry copy with `io.CopyN` or an `io.LimitReader`/counting writer bounded by a configurable maximum (and optionally cross-check against `zip.File.UncompressedSize64` before starting the copy, rejecting entries that individually or cumulatively exceed the configured limit), aborting extraction and cleaning up partial output when exceeded. This limit should be configurable similarly to `MaxUploadedArchiveSize`, and should apply to artifact and cache extraction paths (`extractZipFileEntry`, and its analogues in `helpers/archives/` for other formats and `commands/helpers/archive/tarzstd/tarzstd_extractor.go`, which has the same unbounded `io.Copy` pattern).

### Proof of Concept
```go
// helpers/archives/zip_extract_zipbomb_test.go
func TestExtractZipFile_DecompressionBombIsBounded(t *testing.T) {
    tempFile, _ := os.CreateTemp("", "archive")
    defer os.Remove(tempFile.Name())

    zw := zip.NewWriter(tempFile)
    w, _ := zw.CreateHeader(&zip.FileHeader{
        Name:   "bomb.bin",
        Method: zip.Deflate,
    })
    // Write a highly compressible pattern (e.g. all zero bytes) totaling
    // e.g. 10 GB logical size through the deflate writer, yielding a
    // very small compressed size (a few KB/MB).
    zeroBuf := make([]byte, 1<<20) // 1 MiB of zeros
    const targetLogicalBytes = 10 * 1 << 30 // 10 GiB
    var written int64
    for written < targetLogicalBytes {
        n, _ := w.Write(zeroBuf)
        written += int64(n)
    }
    zw.Close()
    tempFile.Close()

    err := ExtractZipFile(tempFile.Name())

    // Expected (after fix): extraction aborts once a configured
    // decompressed-size cap is exceeded, returning an error, and no
    // multi-gigabyte file is left on disk.
    require.Error(t, err)
    fi, statErr := os.Stat("bomb.bin")
    if statErr == nil {
        defer os.Remove("bomb.bin")
        assert.Less(t, fi.Size(), int64(1<<30), "extracted file should not exceed configured cap")
    }

    // Current behavior (no fix): err is nil and bomb.bin is ~10 GiB,
    // demonstrating the unbounded decompression.
}
```
Run this test in a disk-quota-limited sandbox (e.g., a small tmpfs mount or cgroup with a disk-usage limit) to assert the extraction is aborted before exhausting the quota, rather than filling it and returning `ENOSPC` mid-copy. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** helpers/archives/zip_extract.go (L41-59)
```go
func extractZipFileEntry(file *zip.File) (err error) {
	var out *os.File
	in, err := file.Open()
	if err != nil {
		return err
	}
	defer func() { _ = in.Close() }()

	// Remove file before creating a new one, otherwise we can error that file does exist
	_ = os.Remove(file.Name)
	out, err = os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, file.Mode().Perm())
	if err != nil {
		return err
	}
	defer func() { _ = out.Close() }()
	_, err = io.Copy(out, in)

	return
}
```

**File:** helpers/archives/zip_extract.go (L85-120)
```go
func ExtractZipArchive(archive *zip.Reader) error {
	tracker := newPathErrorTracker()

	for _, file := range archive.File {
		if err := errorIfGitDirectory(file.Name); tracker.actionable(err) {
			printGitArchiveWarning("extract")
		}

		if err := extractZipFile(file); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}
	}

	for _, file := range archive.File {
		if err := lchmod(file.Name, file.Mode()); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}

		// Process zip metadata
		if err := processZipExtra(&file.FileHeader); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}
	}

	return nil
}

func ExtractZipFile(fileName string) error {
	archive, err := zip.OpenReader(fileName)
	if err != nil {
		return err
	}
	defer func() { _ = archive.Close() }()

	return ExtractZipArchive(&archive.Reader)
}
```

**File:** commands/helpers/artifacts_downloader.go (L125-141)
```go
	f, size, format, err := openArchive(file.Name())
	if err != nil {
		logrus.Fatalln(err)
	}
	defer f.Close()

	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	// Extract artifacts file
	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
	}
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

**File:** docs/configuration/advanced-configuration.md (L1475-1475)
```markdown
| `MaxUploadedArchiveSize` | int64   | Limit, in bytes, of the cache archive being uploaded to cloud storage. A malicious actor can work around this limit so the GCS adapter enforces it through the X-Goog-Content-Length-Range header in the signed URL. You should also set the limit on your cloud storage provider. |
```
