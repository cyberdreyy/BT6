### Title
`extractZipFileEntry`/`extractZipFile` extract raw zip entry names with no root confinement, allowing `..` and absolute-path escape - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipFile`, `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, and `extractZipFileEntry` in `helpers/archives/zip_extract.go` use `file.Name` from the zip header directly in `os.Mkdir`, `os.MkdirAll(filepath.Dir(...))`, `os.OpenFile`, and `os.Symlink` calls with zero path-traversal or root-confinement validation. The only check performed on entry names is `errorIfGitDirectory`, which merely detects a leading `.git` segment and is only a warning, not a path-safety guard.

### Finding Description
The zip-format artifact/cache extraction path (used by `ArtifactsDownloaderCommand.Execute` and `CacheExtractorCommand.Execute` via `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`, whose `Extract()` calls `archives.ExtractZipArchive(zr)` directly) processes every `*zip.File` entry: [1](#0-0) 

`extractZipFileEntry` calls `os.Remove(file.Name)` then `os.OpenFile(file.Name, ...)` using the raw archive-supplied name with no sanitization. `extractZipFile` similarly does `os.MkdirAll(filepath.Dir(file.Name), 0o777)`: [2](#0-1) 

`ExtractZipArchive` runs this for every file entry and only screens for `.git`-prefixed names (a warning, not a traversal guard): [3](#0-2) [4](#0-3) 

Go's `archive/zip` package (`zip.Reader`/`zip.File`) does **not** sanitize `File.Name` for `..` segments, absolute paths, or backslash-based traversal when the caller uses the raw field directly (that protection only exists inside `zip.Reader.Open`, which this code doesn't use). An attacker who controls the artifact/cache archive content (any pipeline author, since artifacts/cache are produced by job scripts and consumed later by Runner without additional validation) can craft an entry named e.g. `../../../../etc/passwd` or `..\\..\\evil.sh` (on Windows) or an absolute path, and `extractZipFileEntry`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry` will write outside the intended extraction directory.

This directly contrasts with the sibling extractor `commands/helpers/archive/tarzstd/tarzstd_extractor.go`, which explicitly builds `path = filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects it if it doesn't have `e.dir` as a prefix: [5](#0-4) 

The zip path has no equivalent check. Additionally, `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go` doesn't even use its `dir` field to `Chdir` or join paths — it is discarded entirely: [6](#0-5) 

The extraction relies entirely on the process's current working directory (set by the caller, e.g. `ArtifactsDownloaderCommand.Execute`/`CacheExtractorCommand.Execute` via `os.Getwd()`), meaning any `..`-prefixed entry name escapes that working directory with no server-side or Runner-side rejection.

### Impact Explanation
A job or pipeline author that controls artifact/cache content (their own job's `artifacts:paths` output, or a cache entry populated by a compromised/malicious build step) can craft a zip whose entries traverse outside the build directory. When this artifact/cache is later restored by another job on the same runner host/container (dependent job, `needs:`/`dependencies:` artifact fetch, or cache restore in a subsequent pipeline run sharing the same runner/host), the write can overwrite files in the runner's temp directory, other checkout paths, or (for shell/host-based executors) files reachable by the runner user outside the job sandbox — enabling cross-job state tampering, corruption of git checkout files used by other jobs, or planting of executable content later invoked by CI scripts, matching the "protected-ref escalation or cross-job state tampering via path-root escape" impact class.

### Likelihood Explanation
Feasibility is high: constructing a zip file with crafted entry names (`..` segments, absolute paths) is trivial with `archive/zip`, and no privilege beyond producing a normal job artifact or cache entry is needed. The bug is deterministically reachable any time `ExtractZipArchive`/`ExtractZipFile` is invoked (artifact download, `cache-extractor` command with zip format), and no existing check in the traversed code paths (`errorIfGitDirectory`, `lchmod`, `processZipExtra`) validates path containment.

### Recommendation
Before performing any filesystem operation on `file.Name`, compute the target path relative to the extraction root (analogous to the tarzstd extractor): `target := filepath.Join(root, file.Name)`, then verify `target` is contained in `root` (e.g. via `filepath.Rel` and rejecting results starting with `..`, or the `filepath.Abs` + `strings.HasPrefix` pattern already used in `tarzstd_extractor.go`). Reject or skip entries that fail this check, and thread the extraction root explicitly through `ExtractZipArchive`/`extractZipFile`/`extractZipFileEntry`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry` instead of relying implicitly on the process CWD. Also wire `ziplegacy` extractor's `dir` field into the extraction call instead of discarding it.

### Proof of Concept
Go unit test to add to `helpers/archives/zip_extract_test.go`:
```go
func TestExtractZipFileEscapesRoot(t *testing.T) {
    testOnArchive(t, func(t *testing.T, archive *zip.Writer) {
        w, err := archive.Create("../outside_root_evil.txt")
        require.NoError(t, err)
        _, err = w.Write([]byte("pwned"))
        require.NoError(t, err)
    }, func(t *testing.T, fileName string) {
        tmpDir := t.TempDir()
        oldWd, _ := os.Getwd()
        require.NoError(t, os.Chdir(tmpDir))
        defer os.Chdir(oldWd)

        err := ExtractZipFile(fileName)
        require.NoError(t, err)

        // Assert the file escaped tmpDir into its parent - proving root escape
        _, statErr := os.Stat(filepath.Join(filepath.Dir(tmpDir), "outside_root_evil.txt"))
        assert.NoError(t, statErr, "entry escaped intended extraction root")
        os.Remove(filepath.Join(filepath.Dir(tmpDir), "outside_root_evil.txt"))
    })
}
```
Expected (current, vulnerable) result: the assertion passes, proving the file was created outside `tmpDir`. After the fix, `ExtractZipFile` should either return an error for the traversal entry or skip it, and the file should not exist outside `tmpDir`.

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
