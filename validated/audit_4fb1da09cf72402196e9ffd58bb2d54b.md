This confirms the vulnerability. `openArchive` in `commands/helpers/artifacts_downloader.go` selects the format purely by inspecting the first 4 bytes of the downloaded blob (zstd/gzip magic bytes), and defaults to `archive.Zip` for anything else — meaning an attacker who controls the cache blob content fully controls whether the zip (no chroot) or tarzstd (chroot-enforced) extraction path is taken.

### Title
Zip cache/artifact extraction path lacks directory-containment check present in tar+zstd path, enabling path-traversal writes - ([File: commands/helpers/archive/ziplegacy/zip_legacy_extractor.go])

### Summary
The tar+zstd extractor enforces a chroot-style containment check (`filepath.Abs` + `strings.HasPrefix(path, e.dir+separator)`) before writing any entry, but the legacy zip extractor discards the `dir` parameter entirely and calls `archives.ExtractZipArchive(zr)`, which writes files using the raw, attacker-controlled `zip.File.Name` relative to the process's current working directory with no path sanitization.

### Finding Description
`CacheExtractorCommand.Execute` (`commands/helpers/cache_extractor.go:646-663`) and `ArtifactsDownloaderCommand.Execute` (`commands/helpers/artifacts_downloader.go:125-140`) both call `openArchive(c.File)` to determine the format by sniffing the first 4 bytes of the downloaded blob — zstd magic → `TarZstd`, gzip magic → `Gzip`, anything else → default `archive.Zip` [1](#0-0) . Since this sniff is purely content-based and the blob is fully attacker-supplied (via cache key collision or MITM of a presigned URL, e.g. under `FF_USE_PARALLEL_CACHE_TRANSFER`), the attacker fully controls whether the zip or tarzstd path is chosen.

Both formats' extractors receive the same `dir` (the process `wd` from `os.Getwd()`) via `archive.NewExtractor(format, f, size, wd)` [2](#0-1) , and the `NewExtractorFunc` signature is `func(r io.ReaderAt, size int64, dir string) (Extractor, error)` [3](#0-2) .

The tarzstd extractor uses `dir` for containment enforcement: [4](#0-3) 

The ziplegacy extractor stores `dir` in its struct but never uses it — `Extract` just calls `archives.ExtractZipArchive(zr)` with no directory argument at all: [5](#0-4) 

Inside `ExtractZipArchive`, each entry is written via `extractZipFile(file)` which uses `file.Name` directly: [6](#0-5) 

`extractZipFile` calls `os.MkdirAll(filepath.Dir(file.Name), ...)` and `os.OpenFile(file.Name, ...)` with no `filepath.Clean`/`filepath.Abs`/prefix check against any base directory [7](#0-6) . Go's `archive/zip` package does **not** sanitize `zip.File.Name` when the caller iterates `archive.File` directly and uses `.Name` (that protection only applies to `zip.Reader.Open`, which isn't used here). A `zip.File.Name` of `../../etc/cron.d/evil` or an absolute path is passed through unmodified, so `extractZipFile`/`os.OpenFile` will write outside the intended job working directory, wherever the helper process's CWD happens to be. `extractZipSymlinkEntry` has the identical issue for symlink targets/paths. The only guard present, `errorIfGitDirectory`, only checks for `.git` directory names and is unrelated to path traversal [8](#0-7) .

### Impact Explanation
A malicious or MITM'd cache/artifact zip blob can write arbitrary files at attacker-chosen relative or absolute paths reachable by the `gitlab-runner-helper` process (which typically runs with the build container/host user's privileges), breaking the "file operations must stay within intended build/cache/artifact roots" invariant. Depending on the executor and helper's working directory/privileges, this can allow overwriting files outside the job workspace (persistent tampering) or planting files an later process/job might execute.

### Likelihood Explanation
Requires the attacker to control the bytes of a cache or artifact blob that the runner will download and extract with the zip format selected (achievable since format is content-sniffed and defaults to zip for non-gzip/non-zstd content) — e.g., via a cache key collision within the attacker's own scope, or interception of a presigned URL response under `FF_USE_PARALLEL_CACHE_TRANSFER`/GoCloud transfer. This is fully reproducible with a static PoC zip archive; no timing race or admin compromise needed once the blob is attacker-controlled.

### Recommendation
Make `ziplegacy.extractor.Extract` pass `e.dir` into path resolution and enforce the same containment check as `tarzstd`: for every `zip.File.Name` (and symlink target), compute `filepath.Abs(filepath.Join(e.dir, file.Name))`, verify it has `e.dir+separator` prefix (or equals `e.dir`), and reject/skip entries that escape, mirroring `commands/helpers/archive/tarzstd/tarzstd_extractor.go:57-64`. This should be applied uniformly inside `helpers/archives/zip_extract.go`'s `ExtractZipArchive`/`extractZipFile`/`extractZipSymlinkEntry` so all zip consumers (including `ExtractZipFile`, used elsewhere) are protected, not just the archive-package call site.

### Proof of Concept
Go unit test in `commands/helpers/archive/ziplegacy` (or `helpers/archives`):
```go
func TestZipExtract_PathTraversalRejected(t *testing.T) {
    dir := t.TempDir()
    outside := filepath.Join(filepath.Dir(dir), "pwned.txt")
    defer os.Remove(outside)

    buf := &bytes.Buffer{}
    zw := zip.NewWriter(buf)
    w, _ := zw.Create("../pwned.txt")
    w.Write([]byte("evil"))
    zw.Close()

    r := bytes.NewReader(buf.Bytes())
    ext, err := ziplegacy.NewExtractor(r, int64(r.Len()), dir)
    require.NoError(t, err)

    err = ext.Extract(context.Background())
    // Expected (after fix): error returned, e.g. "cannot be extracted outside of chroot"
    assert.Error(t, err)
    _, statErr := os.Stat(outside)
    assert.True(t, os.IsNotExist(statErr), "file must not be written outside dir")
}
```
Differential test: run the same traversal-entry payload (`../evil`) through both `tarzstd.NewExtractor` (tar+zstd equivalent) and `ziplegacy.NewExtractor`, and assert both return the same class of error and neither creates a file outside `dir`. Currently the tar path rejects (`cannot be extracted outside of chroot`) while the zip path silently succeeds and writes `outside`.

### Citations

**File:** commands/helpers/artifacts_downloader.go (L148-172)
```go
func openArchive(filename string) (*os.File, int64, archive.Format, error) {
	format := archive.Zip

	f, err := os.Open(filename)
	if err != nil {
		return nil, 0, format, err
	}

	var magic [4]byte
	_, _ = f.Read(magic[:])
	_, _ = f.Seek(0, io.SeekStart)
	switch {
	case bytes.HasPrefix(magic[:], zstMagic):
		format = archive.TarZstd
	case bytes.HasPrefix(magic[:], gzipMagic):
		format = archive.Gzip
	}

	fi, err := f.Stat()
	if err != nil {
		f.Close()
		return nil, 0, format, err
	}

	return f, fi.Size(), format, nil
```

**File:** commands/helpers/cache_extractor.go (L655-655)
```go
	extractor, err := archive.NewExtractor(format, f, size, wd)
```

**File:** commands/helpers/archive/archive.go (L61-63)
```go
// NewExtractorFunc is a function that can be registered (with Register()) and
// used to instantiate a new extractor (with NewExtractor()).
type NewExtractorFunc func(r io.ReaderAt, size int64, dir string) (Extractor, error)
```

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L57-64)
```go
		var path string
		path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))
		if err != nil {
			return err
		}
		if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir {
			return fmt.Errorf("%s cannot be extracted outside of chroot (%s)", path, e.dir)
		}
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L24-32)
```go
// Extract extracts files from the reader to the directory passed to
// NewZipExtractor.
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
```

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

**File:** helpers/archives/zip_extract.go (L61-83)
```go
func extractZipFile(file *zip.File) (err error) {
	// Create all parents to extract the file
	err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
	if err != nil {
		return err
	}

	switch file.Mode() & os.ModeType {
	case os.ModeDir:
		err = extractZipDirectoryEntry(file)

	case os.ModeSymlink:
		err = extractZipSymlinkEntry(file)

	case os.ModeNamedPipe, os.ModeSocket, os.ModeDevice:
		// Ignore files of these types
		logrus.Warningf("File ignored: %q", file.Name)

	default:
		err = extractZipFileEntry(file)
	}
	return
}
```

**File:** helpers/archives/zip_extract.go (L88-90)
```go
	for _, file := range archive.File {
		if err := errorIfGitDirectory(file.Name); tracker.actionable(err) {
			printGitArchiveWarning("extract")
```
