### Title
Zip artifact/cache extraction lacks path-containment check, allowing path traversal outside the job's build directory - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipFile` builds destination paths directly from the attacker-controlled `zip.File.Name` field and calls `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and subsequent file/symlink writes with no check that the resolved path stays inside the intended extraction root. This is unlike the sibling tar+zstd extractor, which explicitly validates containment before any write.

### Finding Description
`extractZipFile` (helpers/archives/zip_extract.go:61-83) does: [1](#0-0) 
It never joins `file.Name` against a fixed extraction root nor validates that the resulting absolute path is a descendant of that root. The only validation applied per-entry is `errorIfGitDirectory`, which only rejects `.git`-prefixed names, not `..` traversal: [2](#0-1) [3](#0-2) 

The zip extractor path is reached via `ExtractZipArchive` → `extractZipFile`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry`/`extractZipFileEntry`, all of which use `file.Name` as-is (`os.Mkdir(file.Name, ...)`, `os.Symlink(string(data), file.Name)`, `os.OpenFile(file.Name, ...)`).

Critically, the legacy zip extractor wrapper that Runner actually invokes for cache/artifact extraction (`ziplegacy.extractor.Extract`) accepts a `dir` field but never uses it to scope extraction: [4](#0-3) 
Containment is left entirely to whatever the process current working directory happens to be (set by the caller, e.g. `CacheExtractorCommand.Execute` via `os.Getwd()`), and zip entry names with `../` segments are joined relative to that cwd with no clamping.

Contrast this with `tarzstd_extractor.go`, which computes an absolute path and explicitly rejects escapes: [5](#0-4) 
No equivalent check exists anywhere in the zip extraction path (`helpers/archives/zip_extract.go`, `path_check_helper.go`, or `ziplegacy/zip_legacy_extractor.go`).

Attacker-controlled input: a job (or an artifact/cache producer job in the same or a different pipeline that the attacker controls, e.g. via `CI_JOB_TOKEN` reused across stages, or simply crafting an artifact upload) can produce a ZIP whose `zip.File.Name` entries contain sequences like `../../../victim-project/some/file`. When another job on the same host (shell executor, or docker executor with host-shared builds dir / shared cache volumes) later runs `gitlab-runner cache-extractor` or `gitlab-runner artifacts-downloader` while its cwd is the intended build/cache directory, `filepath.Dir(file.Name)` combined with `..` segments resolves outside that directory, and `os.MkdirAll`/`os.OpenFile`/`os.Symlink` will happily create/overwrite files there.

### Impact Explanation
On a shared-filesystem executor (shell executor, or Docker executor configured with a shared/host `builds`/`cache` volume — both are supported, non-exotic Runner configurations, not "admin-only" hardening choices), a crafted cache or artifact archive can write files/directories/symlinks outside its own job's build directory. If directory names of concurrent/sibling jobs are guessable or predictable (e.g., disabled `use_unique_dirs`/shared cache paths, or well-known `CI_PROJECT_PATH`-based subpaths under `CI_BUILDS_DIR`), this permits cross-job/cross-project file planting or overwrite — a concrete breach of the "file operations must stay within intended build/cache/artifact roots" invariant.

### Likelihood Explanation
- Preconditions: attacker must be able to supply an artifact or cache zip that Runner will later extract (trivial — artifacts/caches are fully attacker-controlled job outputs), and the runner must be a shared-filesystem executor (shell or Docker with shared builds/cache dir), which is a common, legitimate configuration, not solely a discouraged admin choice.
- Feasibility: constructing a zip with `..`-laden `File.Name` values is straightforward with any zip library; Go's `archive/zip` reader does not sanitize `File.Name` for `Open()`-style helpers used here (the code does not use `zip.Reader.Open`, which has some protections — it operates on raw `zip.File.Name` directly).
- Repeatability: fully deterministic given a fixed relative path from the victim job's cwd to the attacker's target.

### Recommendation
In `helpers/archives/zip_extract.go`, before any filesystem operation in `extractZipFile`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry`/`extractZipFileEntry`, resolve the entry against a fixed extraction root and reject entries whose cleaned absolute path is not a descendant of that root — mirroring the containment check already implemented in `commands/helpers/archive/tarzstd/tarzstd_extractor.go` (lines 57-64). Additionally, make `ziplegacy.extractor.Extract` actually use `e.dir` to scope extraction (currently unused) rather than relying on process cwd.

### Proof of Concept
```go
func TestExtractZipFile_PathTraversalEscapesRoot(t *testing.T) {
    root := t.TempDir()
    outsideMarker := filepath.Join(filepath.Dir(root), "escaped-"+filepath.Base(root)+".txt")
    defer os.Remove(outsideMarker)

    origWd, _ := os.Getwd()
    defer os.Chdir(origWd)
    require.NoError(t, os.Chdir(root))

    buf := new(bytes.Buffer)
    zw := zip.NewWriter(buf)
    w, _ := zw.Create("../escaped-" + filepath.Base(root) + ".txt")
    _, _ = w.Write([]byte("cross-job-contamination"))
    require.NoError(t, zw.Close())

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)
    require.NoError(t, archives.ExtractZipArchive(zr))

    // Assert: file must NOT exist outside root; currently it DOES.
    _, err = os.Stat(outsideMarker)
    assert.True(t, os.IsNotExist(err), "zip entry escaped extraction root: %s", outsideMarker)
}
```
Expected today: the assertion fails (file is created outside `root`), proving the traversal. After adding a containment check equivalent to the tarzstd extractor's, the entry should be rejected/skipped and the assertion should pass.

### Citations

**File:** helpers/archives/zip_extract.go (L61-66)
```go
func extractZipFile(file *zip.File) (err error) {
	// Create all parents to extract the file
	err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
	if err != nil {
		return err
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L12-32)
```go
// extractor is a zip stream extractor.
type extractor struct {
	r    io.ReaderAt
	size int64
	dir  string
}

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
