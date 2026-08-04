### Title
Zip artifact extraction lacks path-traversal protection, allowing writes outside the job workspace - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipFile()` in `helpers/archives/zip_extract.go` writes archive entries using the raw `zip.File.Name` field with no `filepath.Join(dir, ...)` normalization and no containment check, while the `ziplegacy` extractor (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`) never even passes its `dir` field into the extraction call. This is inconsistent with `tarzstd_extractor.go`, which explicitly joins entries against `e.dir` and rejects any resolved path that escapes it. A malicious artifact zip with entries like `../../etc/cron.d/evil` can therefore be extracted outside the job workspace when `artifacts-downloader` selects the zip format.

### Finding Description
`ArtifactsDownloaderCommand.Execute` in [1](#0-0)  downloads the artifact, sniffs magic bytes via `openArchive()` (zip is the default fallback format when magic bytes don't match zstd/gzip, see [2](#0-1) ), then dispatches to `archive.NewExtractor(format, f, size, wd)`.

For the `zip` format, this resolves to `ziplegacy.NewExtractor`, whose `Extract` method stores `dir` in the struct but never uses it — it calls `archives.ExtractZipArchive(zr)` directly: [3](#0-2) .

`ExtractZipArchive` iterates zip entries and calls `extractZipFile(file)` for each, which uses `file.Name` verbatim for `os.MkdirAll(filepath.Dir(file.Name), ...)`, `os.OpenFile(file.Name, ...)`, `os.Symlink(..., file.Name)`, and `os.Mkdir(file.Name, ...)` — no join against a base directory, no `filepath.Abs`, and no `strings.HasPrefix` containment check: [4](#0-3) .

Go's standard `archive/zip` package does not sanitize `File.Name` for `../` traversal sequences — that responsibility falls to the caller, and here it is entirely missing. This contrasts directly with the tar/zstd extractor, which does the join+chroot check: [5](#0-4) .

Because extraction happens relative to the process's current working directory (the extractor never `chdir`s or joins against `wd`), a `../../` prefixed entry name resolves outside the job's build directory, giving an attacker-controlled write path anywhere the runner process has filesystem permissions (subject to OS permissions of the account running the job, e.g., shell/exec executors).

### Impact Explanation
An unprivileged pipeline author who controls artifact content from an earlier job/stage can craft a zip artifact with path-traversal entry names. When a downstream job (or a manually triggered `artifacts-downloader` invocation, which is how GitLab Runner fetches dependency artifacts) extracts that artifact and the zip format is selected, files can be written outside the intended workspace root — e.g., overwriting arbitrary files reachable by the executor's OS user. This is a concrete build/artifact-root escape, matching the scoped impact (unauthorized file write outside build root), limited by the OS-level permissions of the process/user running the extraction (most severe on shell executors with elevated permissions, weaker but still artifact-scope-violating on containerized executors sharing the workspace mount).

### Likelihood Explanation
Fully attacker-reachable with no special privileges: any pipeline author can control artifact contents uploaded from a job, and the artifact is later downloaded and extracted by a dependent job automatically through normal GitLab CI dependency/artifact mechanics — no admin action or config change required. The zip format is chosen automatically whenever the artifact isn't zstd/gzip-magic-prefixed (`openArchive` default), so simply uploading a standard zip artifact (as most artifacts are) is sufficient to route to the vulnerable code path.

### Recommendation
In `extractZipFile` (and the directory/symlink entry helpers) in `helpers/archives/zip_extract.go`, resolve each entry against a base extraction directory using `filepath.Join(dir, file.Name)` followed by `filepath.Abs` normalization and a `strings.HasPrefix(resolved, dir+string(filepath.Separator))` (or equivalent) containment check, mirroring `tarzstd_extractor.go`. Additionally, thread the `dir` parameter from `ziplegacy.extractor.Extract` through to `ExtractZipArchive`/`extractZipFile` instead of discarding it, and reject or skip entries that escape the target directory.

### Proof of Concept
Go unit test for `helpers/archives/zip_extract.go`:
```go
func TestExtractZipArchive_PathTraversal(t *testing.T) {
    tmpDir := t.TempDir()
    outsideMarker := filepath.Join(t.TempDir(), "evil")

    // Build in-memory zip with traversal entry
    buf := &bytes.Buffer{}
    zw := zip.NewWriter(buf)
    w, _ := zw.Create("../../../../" + outsideMarker) // or relative traversal from tmpDir
    _, _ = w.Write([]byte("pwned"))
    _ = zw.Close()

    zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))

    prevWd, _ := os.Getwd()
    _ = os.Chdir(tmpDir)
    defer os.Chdir(prevWd)

    err := ExtractZipArchive(zr) // no dir param taken -> should fail with containment error
    require.Error(t, err) // FAILS today: err is nil and file is written outside tmpDir

    _, statErr := os.Stat(outsideMarker)
    require.True(t, os.IsNotExist(statErr), "traversal entry should not be written outside target dir")
}
```
Expected today: the file is written outside `tmpDir` because no join/containment check exists, demonstrating the escape. After the fix, `ExtractZipArchive` (updated to accept and enforce a base `dir`) should return an error and no file should exist at `outsideMarker`.

### Citations

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

**File:** commands/helpers/artifacts_downloader.go (L148-164)
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
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L26-32)
```go
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
```

**File:** helpers/archives/zip_extract.go (L41-83)
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
