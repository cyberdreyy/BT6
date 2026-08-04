### Title
`extractZipSymlinkEntry` (and sibling zip-extraction functions) perform no root-confinement check at all, allowing Zip Slip path traversal - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipSymlinkEntry`, `extractZipFileEntry`, and `extractZipDirectoryEntry` in `helpers/archives/zip_extract.go` use `file.Name` directly to call `os.Remove`/`os.Symlink`/`os.OpenFile`/`os.Mkdir` with no canonicalization or containment check against an extraction root. This is not merely a "separator normalization mismatch" between a validation step and the final write — there is no validation step at all in this code path, so any attacker-controlled entry name (`../`, absolute paths, backslash sequences on Windows, or symlink targets) is used verbatim.

### Finding Description
`extractZipFile` (helpers/archives/zip_extract.go:61-83) dispatches based on `file.Mode()` to `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, or `extractZipFileEntry`. All three functions consume `file.Name` unchanged: [1](#0-0) [2](#0-1) 

The top-level driver `ExtractZipArchive` only checks for `.git` directory entries via `errorIfGitDirectory` — it performs no path-containment validation: [3](#0-2) 

`errorIfGitDirectory`/`isPathAGitDirectory` only look at whether the first path segment is `.git`; they do nothing to reject `..`, absolute paths, or mixed separators: [4](#0-3) 

This is invoked by the legacy zip extractor used for cache/artifact extraction, which passes the raw `zip.Reader` straight to `archives.ExtractZipArchive` with no root-confinement wrapper of its own: [5](#0-4) 

By contrast, the tar+zstd extractor used elsewhere in the same package tree explicitly computes `filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects any path that doesn't have `e.dir` as a prefix before writing: [6](#0-5) 

No equivalent check exists anywhere in `helpers/archives/zip_extract.go` for the zip code path. So an attacker who controls the archive (a cache archive uploaded by the job, or an artifact downloaded/extracted by another job/pipeline consuming the artifact) can craft a zip entry named e.g. `../../../../tmp/evil`, or a symlink entry whose target (`data` in `extractZipSymlinkEntry`) points outside the extraction root, and `os.Symlink`/`os.OpenFile` will write there directly — no separator-normalization trick is even required, since there's no check to bypass in the first place.

### Impact Explanation
An unprivileged pipeline author who controls the contents of a job cache or artifact zip archive can cause the Runner to write files or create symlinks outside the intended cache/build/artifact root during extraction on the host running the job (shell/executor working directory), potentially overwriting files elsewhere in the build tree or, via symlink+follow-up write in a later step, files outside it. This matches the scoped "path-root escape leading to stronger-context overwrite" impact.

### Likelihood Explanation
Fully attacker-reachable with no special privileges: any job can produce a `cache: paths` archive or artifact zip with crafted entry names/symlink targets, and the Runner will extract it via the `ziplegacy`/`archives.ExtractZipArchive` path on a subsequent job run (self or restore-cache/artifact-download of another job in the same pipeline/project). The bug is deterministic and trivially reproducible — no need to exploit a subtle separator-normalization race, since containment enforcement is simply absent.

### Recommendation
Add root-confinement validation in `helpers/archives/zip_extract.go` mirroring the tarzstd extractor: before any filesystem operation, resolve `filepath.Join(rootDir, file.Name)` to an absolute path via `filepath.Abs`/`filepath.Clean` and reject entries whose resolved path is not prefixed by `rootDir` (using `filepath.Separator`-aware comparison, not raw string prefix, to avoid partial-segment matches). Apply the same check to symlink targets in `extractZipSymlinkEntry` (reject absolute or `..`-escaping `data` targets) in addition to `file.Name`. Reject or normalize backslash characters on non-Windows platforms so mixed-separator entries can't be used to smuggle path segments past validation.

### Proof of Concept
Go unit test to add to `helpers/archives/zip_extract_test.go`:
```go
func TestExtractZipArchive_PathTraversal(t *testing.T) {
    dir := t.TempDir()
    outside := filepath.Join(filepath.Dir(dir), "zipslip-poc")
    defer os.Remove(outside)

    wd, _ := os.Getwd()
    require.NoError(t, os.Chdir(dir))
    defer os.Chdir(wd)

    buf := new(bytes.Buffer)
    zw := zip.NewWriter(buf)
    f, _ := zw.Create("../zipslip-poc")
    f.Write([]byte("pwned"))
    zw.Close()

    zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    err := archives.ExtractZipArchive(zr)
    require.NoError(t, err)

    // Assert file was written OUTSIDE dir — this should fail once fixed
    _, statErr := os.Stat(outside)
    assert.NoError(t, statErr, "zip entry escaped extraction root")
}
```
Expected (current/vulnerable) result: the assertion passes, proving the file was written outside `dir`. After adding root-confinement validation, `ExtractZipArchive` should return an error and the file should not exist outside `dir`. A second variant should craft a `os.ModeSymlink` entry whose file data (`data` in `extractZipSymlinkEntry`) is `../../etc/passwd`-style, asserting the resulting symlink does not point outside root.

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

**File:** helpers/archives/zip_extract.go (L85-110)
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
