Based on my investigation, the specific "format detection desync" mechanism hypothesized in this question is not what actually exists in the code, but there is a real, related path-safety defect in the same file.

### Title
Missing path-containment validation in legacy zip extractor allows path traversal via crafted entry names - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipFile`, `extractZipSymlinkEntry`, and `extractZipFileEntry` in `helpers/archives/zip_extract.go` use `file.Name` from the zip header directly for `os.MkdirAll`, `os.Symlink`, and `os.OpenFile` without any check that the resolved path stays within the extraction root. [1](#0-0) [2](#0-1)  Unlike the sibling `tarzstd` extractor, which explicitly resolves the absolute path and rejects any entry that would land outside the extraction directory, this legacy zip code path has no such guard. [3](#0-2) 

### Finding Description
The originally-hypothesized bug — that `extractZipSymlinkEntry` classifies an entry as one type during a "validation" pass and a different type during "extraction" — does not exist in this code. There is only a single classification point: `extractZipFile` computes `file.Mode() & os.ModeType` once and immediately dispatches to `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, or `extractZipFileEntry` using that same value [2](#0-1) ; there is no separate detection pass whose result could desync from the extraction pass. The only pre-check performed before extraction is `errorIfGitDirectory`, which only rejects `.git`-prefixed paths and is not a path-containment check [4](#0-3) [5](#0-4) .

However, the actual, adjacent bug is that `extractZipSymlinkEntry` and `extractZipFileEntry` never validate `file.Name` against the extraction root at all — they call `os.Remove(file.Name)`, `os.Symlink(string(data), file.Name)`, and `os.OpenFile(file.Name, ...)` directly, so an attacker-controlled zip entry name containing `../` sequences or an absolute path can write a symlink or file outside the intended directory [6](#0-5) [7](#0-6) . This is used by `ziplegacy.NewExtractor`, which is registered as the default `Zip`/`ZipZstd` extractor unless `FF_USE_FASTZIP` is enabled (in which case `fastzip` — a third-party library with its own containment logic — takes over) [8](#0-7) [9](#0-8) . Job artifacts and caches are both extracted through this path via `ArtifactsDownloaderCommand.Execute`/`CacheExtractorCommand.Execute`, both of which call `openArchive` and then `archive.NewExtractor` on attacker-influenced file content [10](#0-9) [11](#0-10) .

### Impact Explanation
If `FF_USE_FASTZIP` is disabled (the legacy path is active), an attacker who controls artifact or cache archive content (e.g. a job that generates artifacts, or a poisoned cache later restored by another job/pipeline) can place a zip entry named with `../` traversal or an absolute path to write a symlink/file outside the job's working directory, potentially overwriting files elsewhere on the runner host filesystem that the runner process can write to.

### Likelihood Explanation
This requires `FF_USE_FASTZIP` to be off for the legacy `ziplegacy` extractor to be used (its default state was not confirmed in this pass — `helpers/featureflags/flags.go` defines `UseFastzip` but the default flag value could not be verified from the retrieved context). If fastzip is the effective default, `fastzip.NewExtractorFromReader`'s own path-containment logic (external library, not audited here) would need to be bypassed for this to matter, which is outside the scope of this specific file. Confidence in whether this is exploitable **by default** is therefore incomplete.

### Recommendation
Add explicit path-containment validation in `extractZipFile`/`extractZipSymlinkEntry`/`extractZipFileEntry` (mirroring the tarzstd extractor's `filepath.Abs` + `strings.HasPrefix(path, dir+separator)` check) before performing any filesystem write, rejecting entries whose resolved path escapes the extraction root.

### Proof of Concept
```go
func TestExtractZipFile_PathTraversal(t *testing.T) {
    tmpDir := t.TempDir()
    outsideFile := filepath.Join(filepath.Dir(tmpDir), "pwned")
    defer os.Remove(outsideFile)

    archivePath := filepath.Join(tmpDir, "evil.zip")
    f, _ := os.Create(archivePath)
    zw := zip.NewWriter(f)
    w, _ := zw.Create("../pwned")
    w.Write([]byte("owned"))
    zw.Close()
    f.Close()

    oldWd, _ := os.Getwd()
    os.Chdir(tmpDir)
    defer os.Chdir(oldWd)

    err := archives.ExtractZipFile(archivePath)
    require.NoError(t, err)

    // Assert file was written outside tmpDir
    _, statErr := os.Stat(outsideFile)
    assert.NoError(t, statErr, "expected traversal write outside extraction root")
}
```
Expected: with the current code, the file `pwned` is created one directory above the extraction root, confirming the missing containment check. A fix should make this test fail to create the file and instead return an error.

Note: the exact hypothesis in the question (format-classification desync inside `extractZipSymlinkEntry`) is not substantiated by the code — there is no dual detection/extraction pass to desynchronize. This finding instead documents the real, adjacent path-safety gap in the same function/file.

### Citations

**File:** helpers/archives/zip_extract.go (L22-39)
```go
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
```

**File:** helpers/archives/zip_extract.go (L49-51)
```go
	// Remove file before creating a new one, otherwise we can error that file does exist
	_ = os.Remove(file.Name)
	out, err = os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, file.Mode().Perm())
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

**File:** helpers/archives/zip_extract.go (L88-96)
```go
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

**File:** helpers/archives/path_check_helper.go (L13-19)
```go
func isPathAGitDirectory(path string) bool {
	parts := strings.Split(filepath.Clean(path), string(filepath.Separator))
	if len(parts) > 0 && parts[0] == ".git" {
		return true
	}
	return false
}
```

**File:** commands/helpers/archiver.go (L19-36)
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
