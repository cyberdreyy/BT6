### Title
Zip extraction has no root-confinement check — absolute/drive-letter paths in `zip.File.Name` are honored by `os.MkdirAll`/`os.OpenFile`/`lchmod` - ([File: helpers/archives/zip_extract.go])

### Summary
`ExtractZipArchive` and the file-name it iterates on (`file.Name`) are passed unmodified to `os.MkdirAll`, `os.OpenFile`, `os.Mkdir`, `os.Symlink`, and `lchmod`, with no canonicalization or prefix check against a destination root. Unlike the sibling tar+zstd extractor, which explicitly resolves each entry with `filepath.Abs` and verifies a `strings.HasPrefix(path, e.dir+separator)` guard before touching the filesystem, the zip path has no equivalent check anywhere in the call chain from `ExtractZipFile`/`ExtractZipArchive` down through `extractZipFile`.

### Finding Description
`extractZipFile` computes `filepath.Dir(file.Name)` and calls `os.MkdirAll(..., 0o777)` before dispatching to `extractZipDirectoryEntry` (`os.Mkdir(file.Name, ...)`), `extractZipSymlinkEntry` (`os.Symlink(data, file.Name)`), or `extractZipFileEntry` (`os.OpenFile(file.Name, O_WRONLY|O_CREATE|O_TRUNC, ...)`). [1](#0-0) 
None of these paths validate that `file.Name` resolves under any destination root; `file.Name` comes directly from the zip central directory entry supplied by the archive itself. [2](#0-1) 
After all entries are written, `ExtractZipArchive` runs a second pass calling `lchmod(file.Name, file.Mode())` on the same unvalidated names. [3](#0-2) 
The only pre-write check performed is `errorIfGitDirectory`, which only rejects paths whose first cleaned segment is `.git` — it does nothing to stop absolute paths, drive letters, UNC paths, or `..` traversal. [4](#0-3) 

Compare this to `commands/helpers/archive/tarzstd/tarzstd_extractor.go`, which does implement a root-confinement check for tar+zstd archives via `filepath.Abs(filepath.Join(e.dir, hdr.Name))` followed by a `HasPrefix` check against `e.dir`, rejecting anything that escapes the target directory. [5](#0-4) 
The zip legacy extractor (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`), which is the real caller reachable from cache/artifact extraction, receives a `dir` parameter in its constructor but never uses it — `Extract` just opens the zip reader and calls `archives.ExtractZipArchive(zr)` directly, with no join/prefix step at all. [6](#0-5) 
This confirms confinement for zip archives relies entirely on `file.Name` being a well-behaved relative path — i.e., it relies solely on cooperative archive producers (gitlab-runner's own cache/artifact zip builder), not on any enforced invariant.

Because Go's `os.MkdirAll`/`os.OpenFile`/`os.Mkdir`/`os.Symlink` all honor absolute paths (including Windows drive-letter absolute paths like `C:\Windows\System32\evil.dll` and POSIX absolute paths like `/etc/cron.d/x`) exactly as given, and `filepath.IsAbs`/root-join logic is entirely absent from this code path, a zip entry named with such a path will be created/overwritten outside the extraction directory, and `lchmod` will subsequently `chmod` that same absolute path.

### Impact Explanation
If the raw zip bytes reaching `ExtractZipArchive` are attacker-influenced (e.g., a job supplies its own cache/artifact zip that is pulled and extracted by another job/pipeline stage on the same runner, or a compromised/collided cache key causes the runner to download and extract attacker content), the extraction call will write/overwrite arbitrary host files at attacker-chosen absolute paths and then `chmod` them, without any confinement failure. This matches the scoped impact: host-file overwrite/permission tampering usable for persistence across subsequent jobs on that runner (e.g., writing into a shared PATH location, cron directory, or a file relied on by CI on that host/executor).

### Likelihood Explanation
Preconditions require the attacker to control the raw archive bytes that get fed into `ExtractZipArchive`/`ExtractZipFile` — this is the normal cache/artifact download-then-extract flow (`commands/helpers/cache_extractor.go` → `archive.NewExtractor` → `ziplegacy.extractor.Extract` → `archives.ExtractZipArchive`), which any pipeline author already controls the contents of via `cache:` / `artifacts:` definitions. No admin privilege or MITM is needed — a job simply needs to produce or supply a zip whose central directory contains absolute/drive-letter entry names, which the `archive/zip` package permits since it does not itself sanitize names. This is straightforward to reproduce with a hand-crafted zip.

### Recommendation
Add the same root-confinement logic used in `tarzstd_extractor.go` to the zip extraction path: pass a destination directory into `ExtractZipArchive`/`extractZipFile`, join it with `file.Name`, canonicalize via `filepath.Abs`/`filepath.Clean`, and reject (fail closed, do not write or chmod) any entry whose resolved path does not have the destination directory as a strict prefix. This check must run before `os.MkdirAll`/`os.OpenFile`/`os.Mkdir`/`os.Symlink` in `extractZipFile`, and before the `lchmod` pass in `ExtractZipArchive`. Additionally wire the `dir` field already present in `ziplegacy.extractor` into this check instead of discarding it.

### Proof of Concept
Go unit test in `helpers/archives/zip_extract_test.go`:
```go
func TestExtractZipArchiveRejectsAbsolutePath(t *testing.T) {
    tmpDir := t.TempDir()
    buf := new(bytes.Buffer)
    zw := zip.NewWriter(buf)
    var evilName string
    if runtime.GOOS == "windows" {
        evilName = `C:\Windows\Temp\gitlab-runner-poc.txt`
    } else {
        evilName = "/tmp/gitlab-runner-poc.txt"
    }
    w, _ := zw.Create(evilName)
    w.Write([]byte("pwned"))
    zw.Close()

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)

    err = archives.ExtractZipArchive(zr) // or a root-scoped variant once fixed
    // Expected once fixed: err != nil (rejected) AND the absolute path is NOT created.
    _, statErr := os.Stat(evilName)
    assert.True(t, os.IsNotExist(statErr), "absolute-path entry must not be written outside destination root")
    defer os.Remove(evilName) // cleanup if PoC currently succeeds, proving the bug
}
```
Currently (pre-fix), this test will show the file created at the absolute path and `chmod`'d, demonstrating the missing confinement check.

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

**File:** helpers/archives/zip_extract.go (L98-107)
```go
	for _, file := range archive.File {
		if err := lchmod(file.Name, file.Mode()); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}

		// Process zip metadata
		if err := processZipExtra(&file.FileHeader); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}
	}
```

**File:** helpers/archives/path_check_helper.go (L13-31)
```go
func isPathAGitDirectory(path string) bool {
	parts := strings.Split(filepath.Clean(path), string(filepath.Separator))
	if len(parts) > 0 && parts[0] == ".git" {
		return true
	}
	return false
}

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

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L56-64)
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
