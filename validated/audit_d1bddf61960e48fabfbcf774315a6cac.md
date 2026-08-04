### Title
Path traversal via unsanitized `zip.File.Name` in legacy ZIP extractor allows writing files outside job workspace - ([File: helpers/archives/zip_extract.go])

### Summary
The `ziplegacy` extractor (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`) calls `archives.ExtractZipArchive`, which writes each ZIP entry directly to `file.Name` with no path confinement check, unlike the `tarzstd` extractor which validates `strings.HasPrefix(path, e.dir+string(filepath.Separator))`. An attacker who controls a cache/artifact ZIP's entry names (e.g., `../../../../tmp/pwned`) can write or overwrite files outside the intended extraction directory when this legacy path is used.

### Finding Description
`extractZipFile`/`extractZipFileEntry`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry` in `helpers/archives/zip_extract.go` (lines 12-83) all operate directly on `file.Name` from the ZIP entry, calling `os.MkdirAll(filepath.Dir(file.Name), ...)`, `os.OpenFile(file.Name, ...)`, `os.Symlink(string(data), file.Name)`, and `os.Mkdir(file.Name, ...)`. None of these join `file.Name` against an extraction root, canonicalize the resulting path, or verify it stays within a chroot-like boundary. `ExtractZipArchive` (lines 85-110) iterates `archive.File` and calls `extractZipFile` for each entry without any traversal check.

Critically, the `extractor` in `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go` holds a `dir` field (passed in via `NewExtractor(r, size, dir)`) but never uses it — `Extract()` simply calls `archives.ExtractZipArchive(zr)`, ignoring `e.dir` entirely, so extraction happens relative to the process's current working directory with no directory confinement at all.

This contrasts directly with `commands/helpers/archive/tarzstd/tarzstd_extractor.go` (lines 57-64), which computes `path = filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects the entry with an error if `!strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir`. The ZIP path has no equivalent check.

Since `ziplegacy` is registered as the default ZIP extractor unless `FF_USE_FASTZIP` is enabled (`commands/helpers/archiver.go`, which registers `fastzip` as an override only "if on"), a normal cache/artifact download using the default (non-fastzip) code path is affected.

### Impact Explanation
A CI job author who controls the contents of a cache or artifact archive (uploaded during their own job and later downloaded by a subsequent job/stage, or supplied via `CI_ARTIFACTS`/cache restore flows) can craft ZIP entries with `../` traversal sequences in the entry name. When extracted via the `ziplegacy` extractor invoked by `CacheExtractorCommand.Execute` (`commands/helpers/cache_extractor.go`) or the artifacts-downloader stage, this can overwrite or create arbitrary files reachable by the runner process's filesystem permissions, outside the intended job/cache/artifact root — violating the workspace-confinement invariant.

### Likelihood Explanation
Feasible and repeatable: any pipeline author can produce an artifact/cache ZIP with attacker-chosen entry names (standard ZIP libraries readily allow `../` in file names) and have their own job trigger extraction of it. The only precondition is that the runner is using the legacy zip extractor/decompressor path (default unless `FF_USE_FASTZIP` is enabled), which is a common, non-privileged configuration.

### Recommendation
Add a path-confinement check in `helpers/archives/zip_extract.go` analogous to `tarzstd_extractor.go`: resolve each `file.Name` against the intended extraction root via `filepath.Abs(filepath.Join(dir, file.Name))` and reject/skip entries whose resolved path does not have `dir+string(filepath.Separator)` as a prefix (and isn't equal to `dir`). This requires threading the extraction root (`dir`) through `ExtractZipArchive`/`extractZipFile` and actually using `e.dir` in `zip_legacy_extractor.go` instead of discarding it.

### Proof of Concept
```go
// helpers/archives/zip_extract_traversal_test.go
func TestExtractZipArchive_PathTraversal(t *testing.T) {
    tmpExtractDir := t.TempDir()
    outsideTarget := filepath.Join(filepath.Dir(tmpExtractDir), "pwned")

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    f, _ := zw.Create("../pwned")
    f.Write([]byte("attacker-controlled"))
    zw.Close()

    zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))

    wd, _ := os.Getwd()
    os.Chdir(tmpExtractDir)
    defer os.Chdir(wd)

    err := ExtractZipArchive(zr)
    assert.NoError(t, err)

    // BUG: file lands outside tmpExtractDir
    _, statErr := os.Stat(outsideTarget)
    assert.NoError(t, statErr, "file was written outside extraction root: %s", outsideTarget)
    os.Remove(outsideTarget)
}
```
Expected (buggy) result: the assertion for `outsideTarget` existing passes, proving the write escaped the intended directory. After applying the recommended fix (root-relative check), `ExtractZipArchive`/the fixed `zip_legacy_extractor.go` should return an error for the `../pwned` entry and `outsideTarget` should not exist. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** helpers/archives/zip_extract.go (L85-97)
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L24-33)
```go
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
