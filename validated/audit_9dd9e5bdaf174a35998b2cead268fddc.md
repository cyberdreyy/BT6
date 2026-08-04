Based on my investigation, I found a real, concrete vulnerability in this area, though it manifests slightly differently than the "cleanup follows alias on error path" framing suggests — it's actually a **path-confinement failure across the whole zip legacy extraction flow**, not limited to `extractZipFileEntry`'s error branch.

### Title
Zip legacy extractor lacks symlink/path confinement, allowing writes outside extraction root via attacker-controlled symlink aliases - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipSymlinkEntry` creates filesystem symlinks from fully attacker-controlled archive content (the symlink target is just the raw bytes of the zip entry) with zero validation that the target stays inside the extraction root. Because `extractZipFile`/`extractZipFileEntry`/`extractZipDirectoryEntry` operate on raw `file.Name` values with no root-confinement check (unlike the sibling `tarzstd` extractor, which explicitly validates `filepath.Abs(filepath.Join(e.dir, hdr.Name))` stays under `e.dir`), a later archive entry whose name traverses through that attacker-planted symlink (e.g. `evil/passwd` where `evil` is a symlink to `/etc`) will have its `os.Remove`/`os.OpenFile`/write operations silently follow the symlink alias and land outside the intended cache/artifact directory.

### Finding Description
- `extractZipSymlinkEntry` (helpers/archives/zip_extract.go:22-39) reads the symlink target from the zip entry's file content and calls `os.Symlink(string(data), file.Name)` unconditionally — no check that the resulting link stays within the extraction root, no rejection of absolute targets or `..`-containing targets. [1](#0-0) 
- `extractZipFile` (helpers/archives/zip_extract.go:61-83) dispatches purely on `file.Mode()` and does `os.MkdirAll(filepath.Dir(file.Name), ...)` then calls `extractZipDirectoryEntry`/`extractZipSymlinkEntry`/`extractZipFileEntry` using the raw, attacker-supplied `file.Name` with no `filepath.Clean`/absolute-path/`..`/symlink-escape validation anywhere in this file. [2](#0-1) 
- `extractZipFileEntry` itself (helpers/archives/zip_extract.go:41-59) does `_ = os.Remove(file.Name)` then `os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, ...)`. If an earlier entry in the same zip planted a symlink at an intermediate path component (e.g. `link` → `/etc`), a later entry named `link/passwd` causes both the `Remove` and `OpenFile` calls to resolve through that symlink at the OS level, writing/removing content outside the extraction root. [3](#0-2) 
- Contrast with `commands/helpers/archive/tarzstd/tarzstd_extractor.go`, which explicitly guards against this class of bug by computing `path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejecting any path that doesn't have `e.dir` as prefix — no equivalent check exists in `helpers/archives/zip_extract.go`. [4](#0-3) 
- The legacy zip extractor (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`) receives a `dir` parameter from its caller but never uses it — it just calls `archives.ExtractZipArchive(zr)` directly on the raw reader, so there is no chroot/confinement applied at this layer either. [5](#0-4) 
- The only defensive check present, `errorIfGitDirectory`, only guards against `.git` directory entries and is merely a warning-log trigger via `pathErrorTracker`, not a path-confinement mechanism. [6](#0-5) [7](#0-6) 

This is reachable via any code path that extracts a zip archive built from attacker-controlled content and dispatched to the legacy zip extractor — e.g. cache/artifact extraction (`commands/helpers/cache_extractor.go`, `commands/helpers/artifacts_downloader.go`), both of which call into the `archive` package's extractor selection that can resolve to `ziplegacy.NewExtractor`.

### Impact Explanation
An attacker who controls the contents of a cache or artifact zip archive consumed by a job (their own job, or via a poisoned shared cache key consumed by another job/pipeline) can plant a symlink whose target points outside the intended extraction directory, then include a subsequent zip entry whose name traverses through that symlink. This allows writing or removing files outside the build/cache/artifact root on the runner's filesystem — a path-confinement violation on the executor host running the job. Scoped impact matches "cross-job tampering through cleanup path confusion": file operations escape the intended root due to symlink alias resolution, not a hardened error-path bug specifically.

### Likelihood Explanation
Fully attacker-controlled preconditions: any pipeline author can create a crafted cache/artifact zip with a symlink entry followed by a nested file entry. No admin privilege or special runner configuration is required beyond the legacy zip extractor being in use (it is a supported extractor path, selectable/fallback in `commands/helpers/archive`). The bug is deterministic and repeatable — every extraction of such an archive triggers it, not just on forced error paths.

### Recommendation
Add root-confinement validation in `helpers/archives/zip_extract.go` analogous to `tarzstd_extractor.go`: resolve each entry's target path with `filepath.Abs`/`filepath.EvalSymlinks` against the extraction root and reject entries whose resolved path escapes the root, both for symlink targets (`extractZipSymlinkEntry`) and for any entry name containing symlinked intermediate components. Additionally, `ziplegacy.extractor.Extract` should actually use its `dir` field to enforce a boundary rather than ignoring it.

### Proof of Concept
Go unit test in `helpers/archives/zip_extract_test.go`:
1. Create a zip archive with two entries:
   - `link` — symlink mode, content = absolute path to a temp directory outside the extraction working directory (e.g. `/tmp/outside`).
   - `link/pwned.txt` — regular file with arbitrary content.
2. Call `ExtractZipFile(archivePath)` from within a clean temp extraction directory.
3. Assert that `/tmp/outside/pwned.txt` does NOT exist (expected to fail with current code — it will exist, proving the escape) and that `ExtractZipFile` returns an error rejecting the traversal (expected to fail — currently returns `nil`).

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

**File:** helpers/archives/zip_extract.go (L49-56)
```go
	// Remove file before creating a new one, otherwise we can error that file does exist
	_ = os.Remove(file.Name)
	out, err = os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, file.Mode().Perm())
	if err != nil {
		return err
	}
	defer func() { _ = out.Close() }()
	_, err = io.Copy(out, in)
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L19-32)
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
```

**File:** helpers/archives/path_check_helper.go (L21-31)
```go
func errorIfGitDirectory(path string) *os.PathError {
	if !isPathAGitDirectory(path) {
		return nil
	}

	return &os.PathError{
		Op:   ".git inside of archive",
		Path: path,
		Err:  errors.New("trying to archive or extract .git path"),
	}
}
```
