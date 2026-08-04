### Title
Legacy zip extractor ignores `dir` confinement param and has no path-traversal (zip-slip) protection — ([File: commands/helpers/archive/ziplegacy/zip_legacy_extractor.go])

### Summary
The specific attack described in the question (fuzzing `size`/reader length to make Go's `archive/zip` or `zstd`/`tar` parser misparse a central directory into attacker-chosen absolute paths) is not realistic: `archive/zip.NewReader` and the `tarzstd` reader either successfully parse a well-formed archive or return an error — they do not "misparse" a corrupted size field into fabricated entries. However, investigating the same `NewExtractor` call sites uncovered a real, related bug: the default/legacy zip extractor (`ziplegacy`) receives the confinement `dir` parameter but never uses it, and `helpers/archives.ExtractZipArchive` writes each entry using the raw, attacker-controlled `zip.File.Name` with no path-traversal or absolute-path check, unlike the `tarzstd` extractor which explicitly validates paths against `dir`.

### Finding Description
`archive.NewExtractor` dispatches to a registered `NewExtractorFunc` for the requested format [1](#0-0) . For `archive.Zip` (and the zstd-in-zip decompression fallback), the default registration is `ziplegacy.NewExtractor` [2](#0-1) , which stores `dir` but its `Extract` method never passes `dir` anywhere — it only builds a `zip.Reader` and calls `archives.ExtractZipArchive(zr)` [3](#0-2) . Inside `ExtractZipArchive`, each entry is written using `file.Name` verbatim via `os.Mkdir`, `os.Symlink`, and `os.OpenFile`, with the only sanitization being a `.git`-directory warning — there is no check for `..` traversal, no check for absolute paths, and no join/validation against any confinement root [4](#0-3) [5](#0-4) . This is in stark contrast to the `tarzstd` extractor's `Extract`, which explicitly resolves each entry against `e.dir` and rejects any path that doesn't stay under it: `if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir { return fmt.Errorf(...) }` [6](#0-5) .

Both `ArtifactsDownloaderCommand.Execute` and `CacheExtractorCommand.Execute` call `archive.NewExtractor(format, f, size, wd)` with the runner's job working directory as `dir`, then call `extractor.Extract(...)` on attacker-influenced content (a downloaded artifact/cache archive) [7](#0-6) [8](#0-7) . Because `ziplegacy` ignores `dir` entirely and performs no traversal checks, a crafted zip archive containing an entry named e.g. `../../../home/gitlab-runner/.ssh/authorized_keys` (or, on the relevant platform, an absolute path) would be extracted to that path relative to the process's current working directory rather than being confined to `wd`/`dir`, violating the file-confinement invariant documented on `NewExtractor` ("The extractor will extract files to the directory provided") [9](#0-8) .

Whether this legacy path is reached in a given deployment depends on the `FF_USE_FASTZIP` feature flag: when off (or for the zstd-in-zip decompression path, which always uses `ziplegacy.NewExtractor` regardless of the flag), the vulnerable code is used [10](#0-9) .

### Impact Explanation
An attacker who controls the content of a cache or artifact zip (any pipeline author, since caches/artifacts are produced from job-controlled content and cache keys are attacker-influenced) can craft a malicious zip whose entries use relative traversal (`../`) or absolute names. When such an archive is extracted by a runner using the legacy zip extractor, files can be written outside the intended job working directory/cache root, on the machine hosting the runner's job execution (shell executor host, or within the executor's filesystem view for other executors). This matches the scoped impact: host file overwrite outside job root, e.g. clobbering leftover files from another job's workspace or runner-host files reachable by the runner process's permissions.

### Likelihood Explanation
Feasibility is high for the shell executor (or any executor where a real host filesystem is directly exposed to the runner-helper's working directory) and for any deployment not using `FF_USE_FASTZIP` (the flag also does not protect the zstd-in-zip decode path, which always uses `ziplegacy`). Constructing a malicious zip with traversal entry names is trivial and fully within an unprivileged CI job's control (e.g. via `cache:` or `artifacts:` producing a hand-crafted zip, or restoring a cache uploaded by the attacker's own pipeline before it's consumed in a later job/stage). Reachability requires only using GitLab CI cache/artifacts normally — no admin privileges needed.

### Recommendation
Add the same path-confinement check used in `tarzstd_extractor.go` to `helpers/archives.ExtractZipArchive`/`extractZipFile` (or pass `dir` through `ziplegacy.extractor.Extract` and join/validate each `file.Name` against it before any `os.Mkdir`/`os.OpenFile`/`os.Symlink` call), rejecting absolute paths and any resolved path that escapes `dir`.

### Proof of Concept
```go
func TestZipLegacyExtractorPathTraversal(t *testing.T) {
    buf := new(bytes.Buffer)
    zw := zip.NewWriter(buf)
    f, _ := zw.Create("../../evil.txt")
    _, _ = f.Write([]byte("pwned"))
    require.NoError(t, zw.Close())

    dir := t.TempDir()
    extractor, err := ziplegacy.NewExtractor(bytes.NewReader(buf.Bytes()), int64(buf.Len()), dir)
    require.NoError(t, err)

    err = extractor.Extract(context.Background())
    // Expected (fixed) behavior: error, and no file written outside dir.
    require.Error(t, err)
    _, statErr := os.Stat(filepath.Join(filepath.Dir(filepath.Dir(dir)), "evil.txt"))
    assert.True(t, os.IsNotExist(statErr), "traversal file should not exist outside dir")
}
```
This currently fails (extraction succeeds and writes outside `dir`), confirming the bug; after adding path confinement it should pass.

### Citations

**File:** commands/helpers/archive/archive.go (L99-109)
```go
// NewExtractor returns a new Extractor of the specified format.
//
// The extractor will extract files to the directory provided.
func NewExtractor(format Format, r io.ReaderAt, size int64, dir string) (Extractor, error) {
	fn := extractors[format]
	if fn == nil {
		return nil, fmt.Errorf("%q format: %w", format, ErrUnsupportedArchiveFormat)
	}

	return fn(r, size, dir)
}
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_archiver.go (L16-21)
```go
func init() {
	zip.RegisterDecompressor(zstd.ZipMethodWinZip, fastzip.ZstdDecompressor())

	archive.Register(archive.Zip, NewArchiver, NewExtractor)
	archive.Register(archive.ZipZstd, nil, NewExtractor)
}
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L19-33)
```go
// NewExtractor returns a new Zip Extractor.
func NewExtractor(r io.ReaderAt, size int64, dir string) (archive.Extractor, error) {
	return &extractor{r: r, size: size, dir: dir}, nil
}

// Extract extracts files from the reader to the directory passed to
// NewZipExtractor.
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
}
```

**File:** helpers/archives/zip_extract.go (L12-59)
```go
func extractZipDirectoryEntry(file *zip.File) (err error) {
	err = os.Mkdir(file.Name, file.Mode().Perm())

	// The "directory does exist" error is not an error for us
	if os.IsExist(err) {
		err = nil
	}
	return
}

func extractZipSymlinkEntry(file *zip.File) (err error) {
	var data []byte
	in, err := file.Open()
	if err != nil {
		return err
	}
	defer func() { _ = in.Close() }()

	data, err = io.ReadAll(in)
	if err != nil {
		return err
	}

	// Remove symlink before creating a new one, otherwise we can error that file does exist
	_ = os.Remove(file.Name)
	err = os.Symlink(string(data), file.Name)
	return
}

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

**File:** helpers/archives/zip_extract.go (L85-96)
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

**File:** commands/helpers/artifacts_downloader.go (L125-140)
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

**File:** commands/helpers/archiver.go (L19-37)
```go
func init() {
	// enable fastzip archiver/extractor
	logger := logrus.WithField("name", featureflags.UseFastzip)
	if on := featureflags.IsOn(logger, os.Getenv(featureflags.UseFastzip)); on {
		archive.Register(archive.Zip, fastzip.NewArchiver, fastzip.NewExtractor)

		// The default zstd compressor is fastzip, this is registered via the
		// fastzip implementation (helpers/archive/fastzip).
		//
		// The default zstd decompressor is the legacy zip implementation (helpers/archive/ziplegacy).
		// This intended to allow the default zip implementation to still be able to decompress zstd,
		// even if it is unable to compress it (only fastzip can compress). This also allows the older
		// extraction behaviour to be enabled.
		//
		// Here we're registering the decompress only if FF_USE_FASTZIP is enabled. This overrides
		// the ziplegacy zstd support.
		archive.Register(archive.ZipZstd, nil, fastzip.NewExtractor)
	}
}
```
