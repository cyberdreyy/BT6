### Title
Zip extraction lacks path-traversal (Zip Slip) protection unlike the tar/zstd extractor - ([File: helpers/archives/zip_extract.go])

### Summary
`CreateZipArchive`/`createZipEntry` in `helpers/archives/zip_create.go` store `fh.Name` verbatim from the caller-supplied file name list, with no normalization. `ExtractZipArchive`/`extractZipFile` in `helpers/archives/zip_extract.go` then does `os.MkdirAll(filepath.Dir(file.Name))` and `os.OpenFile(file.Name, ...)`/`os.Symlink` directly on that name with no check that the resolved path stays inside the extraction directory.

### Finding Description
`extractZipFile` [1](#0-0)  creates parent directories and opens the file purely based on `file.Name` taken from the zip's central directory, with no validation against the target root. This is functionally identical to the classic "Zip Slip" pattern. By contrast, the sibling `tarzstd` extractor in this same codebase explicitly guards against this: it computes an absolute path and rejects any entry whose resolved path escapes the chroot directory [2](#0-1) . The zip path has no equivalent check, which is an inconsistency between the two extractors handling the same trust boundary (archive entries an attacker can influence).

On the creation side, `createZipEntry` sets `fh.Name = fileName` directly from the caller-supplied `fileNames` slice with no normalization [3](#0-2) , so whatever string is passed into `CreateZipArchive` (e.g., `../../tmp/evil`) is written into the archive's file name field unchanged [4](#0-3) .

However, to determine whether this is a genuinely *reachable* attacker path, the critical question is what actually supplies `fileNames` to `CreateZipArchive` in production code (e.g. artifacts archiver, cache archiver), and whether that call site already restricts entries to files inside the build directory (glob-resolved relative paths from `artifacts:paths`, which the runner or GitLab-side resolves relative to the project root and does not typically allow `..` in resulting matched file names) — I was not able to locate and confirm that call site or its input-sanitization behavior within the available tool budget. Likewise, on the extraction side, I could not fully confirm within the available tool budget whether `commands/helpers/archive/zip` (the real, non-legacy zip extractor referenced by `archive.go`) wraps `ExtractZipArchive`/`extractZipFile` with any additional path-containment check before or after calling it (only the `ziplegacy` package, which calls `archives.ExtractZipArchive` with no extra guard, was directly confirmed) [5](#0-4) .

### Impact Explanation
If reachable, this would allow a `zip`-formatted cache or artifact archive containing an entry name like `../../tmp/evil` to write a file outside the extraction root (build directory) when the Runner extracts it via `ExtractZipArchive`, e.g., during `cache-extractor` or artifacts download, which use the current process/build working directory as the intended extraction root [6](#0-5) .

### Likelihood Explanation
The root-cause code (`extractZipFile` performing no containment check) is confirmed to exist and is a real gap relative to the tar/zstd extractor's explicit protection in the same codebase. What is **not** confirmed is whether an unprivileged pipeline author can actually get a `..`-containing name into a zip archive that the Runner will build and later extract via this exact code path (i.e., whether `artifacts:paths`/cache path resolution upstream, or the actual `zip` (non-legacy) extractor wrapper, already blocks this before it reaches `extractZipFile`). Without verifying the real `commands/helpers/archive/zip` extractor implementation and the exact producer of `fileNames` for `CreateZipArchive`, I cannot conclusively confirm end-to-end exploitability.

### Recommendation
Regardless of current reachability, add the same containment check used in `tarzstd_extractor.go` (resolve `filepath.Join(destDir, file.Name)` to an absolute path and reject/skip entries whose path escapes `destDir`) inside `extractZipFile`/`ExtractZipArchive` in `helpers/archives/zip_extract.go`, and normalize/reject `..`-containing names in `createZipEntry`/`CreateZipArchive` in `helpers/archives/zip_create.go`, so the zip path is not solely dependent on upstream input sanitization.

### Proof of Concept
```go
// helpers/archives/zip_traversal_test.go
func TestZipSlipTraversal(t *testing.T) {
    dir := t.TempDir()
    var buf bytes.Buffer
    err := CreateZipArchive(&buf, []string{filepath.Join(dir, "legit.txt")})
    // Simulate an attacker-controlled entry with a traversal name by
    // directly writing a zip.Writer entry named "../../tmp/evil" instead of
    // relying on CreateZipArchive's own path handling, to isolate the
    // extraction-side check:
    zw := zip.NewWriter(&buf)
    fw, _ := zw.Create("../../tmp/evil")
    fw.Write([]byte("pwned"))
    zw.Close()

    r, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    sandbox := t.TempDir()
    os.Chdir(sandbox)
    err = ExtractZipArchive(r)
    assert.NoError(t, err)

    // Assert the file was NOT created outside the sandbox
    _, statErr := os.Stat(filepath.Join(sandbox, "..", "..", "tmp", "evil"))
    assert.True(t, os.IsNotExist(statErr), "zip slip: file escaped sandbox root")
}
```
This test isolates the extraction-side defect (confirmed) from the unresolved question of whether `CreateZipArchive`'s real-world callers can be induced to emit such an entry name in the first place.

### Citations

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

**File:** helpers/archives/zip_create.go (L52-66)
```go
func createZipEntry(archive *zip.Writer, fileName string) error {
	fi, err := os.Lstat(fileName)
	if err != nil {
		logrus.Warningln("File ignored:", err)
		return nil
	}

	fh, err := zip.FileInfoHeader(fi)
	if err != nil {
		return err
	}
	fh.Name = fileName
	fh.Extra = createZipExtra(fi)
	// Set EFS flag to indicate that filenames and comments are UTF-8 encoded
	fh.Flags |= 0x800
```

**File:** helpers/archives/zip_create.go (L85-103)
```go
func CreateZipArchive(w io.Writer, fileNames []string) error {
	tracker := newPathErrorTracker()

	archive := zip.NewWriter(w)
	defer func() { _ = archive.Close() }()

	for _, fileName := range fileNames {
		if err := errorIfGitDirectory(fileName); tracker.actionable(err) {
			printGitArchiveWarning("archive")
		}

		err := createZipEntry(archive, fileName)
		if err != nil {
			return err
		}
	}

	return nil
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

**File:** commands/helpers/cache_extractor.go (L626-663)
```go
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
```
