This confirms a real, exploitable path.

### Title
Zip-slip path traversal in `ExtractZipArchive`/`extractZipFile` allows writing files outside the cache extraction directory - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipFile` in `helpers/archives/zip_extract.go` uses `file.Name` from a `*zip.File` entry directly as a filesystem path for `os.Mkdir`/`os.OpenFile`/`os.Symlink`, with no check that the resolved path stays within the extraction working directory. This is reachable from `CacheExtractorCommand.Execute` via the default (`ziplegacy`) zip extractor, which is used whenever `FF_USE_FASTZIP` is not enabled, unlike `tarzstd`'s extractor which explicitly enforces a chroot-style prefix check.

### Finding Description
`extractZipFile` calls `os.MkdirAll(filepath.Dir(file.Name), ...)` and then, depending on entry type, `os.Mkdir(file.Name, ...)`, `os.Symlink(data, file.Name)`, or `os.OpenFile(file.Name, ...)` [1](#0-0) . None of these paths are joined against, or validated to remain within, a base/extraction directory — `file.Name` (attacker-controlled zip entry name) is used verbatim. The only check performed on entry names in `ExtractZipArchive` is `errorIfGitDirectory`, which only rejects `.git`-prefixed paths and is purely a warning, not a security control [2](#0-1) [3](#0-2) .

Contrast with `tarzstd`'s extractor, which computes `path.Join(e.dir, hdr.Name)`, takes `filepath.Abs`, and explicitly rejects any resulting path that isn't prefixed by `e.dir`: `if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir { return fmt.Errorf(...cannot be extracted outside of chroot...) }` [4](#0-3) . The zip legacy extractor has no equivalent check — it simply calls `archives.ExtractZipArchive(zr)` and discards the `dir` field entirely [5](#0-4) .

Call path: `CacheExtractorCommand.Execute` downloads/opens the cache archive and calls `archive.NewExtractor(format, f, size, wd)` followed by `extractor.Extract(context.Background())` [6](#0-5) . By default (no `FF_USE_FASTZIP`), only `gziplegacy`, `raw`, `tarzstd`, and `ziplegacy` are registered [7](#0-6) , so zip caches resolve to `ziplegacy.NewExtractor`, whose `Extract` ignores `dir` and calls `archives.ExtractZipArchive` directly, meaning entries are extracted relative to the process's current working directory (`os.Getwd()`, itself set from `wd` before `chdir`/execution context) with no chroot enforcement [8](#0-7) .

A job that controls cache contents (e.g. by pushing a cache in one stage with a crafted zip containing an entry named `../../../../tmp/pwned` or an absolute path, then having the runner restore that same cache key in a later stage/job) can cause `extractZipFile` to write or overwrite files outside the intended cache/build directory.

### Impact Explanation
Zip-slip arbitrary file write/overwrite outside the cache extraction root. In shared-runner/shared-cache-directory setups, this can overwrite files belonging to another project's checkout, another job's cache, or other files reachable by the runner-helper process's filesystem permissions, matching the scoped impact exactly.

### Likelihood Explanation
Feasible and repeatable by an unprivileged pipeline author: standard `cache:` push/restore functionality lets a job control the exact bytes of the cache archive, and cache-extraction always runs through `CacheExtractorCommand` → the default `ziplegacy` extractor unless `FF_USE_FASTZIP` is enabled (fastzip's `saracen/fastzip` library performs its own path-containment checks, so the vulnerable path is specifically the legacy/default one). No admin action or privilege escalation is required beyond normal CI job authoring.

### Recommendation
Add the same chroot-style containment check used in `tarzstd_extractor.go` to `helpers/archives/zip_extract.go`: resolve each `file.Name` against the intended extraction root via `filepath.Join`/`filepath.Abs`, verify the result has the root as a strict path prefix (or equals the root), and reject/skip entries that resolve outside it (also reject absolute entry names and symlink targets that escape the root). Apply this in `extractZipFile` (or in `ExtractZipArchive` before dispatching to it) so it protects both direct callers of `ExtractZipFile`/`ExtractZipArchive` and the `ziplegacy` extractor which currently discards its `dir` parameter entirely.

### Proof of Concept
```go
func TestExtractZipArchive_PathTraversal(t *testing.T) {
    tmpWorkDir := t.TempDir()
    outsideTarget := filepath.Join(t.TempDir(), "pwned")

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    // relative traversal escaping the intended extraction directory
    escapePath, _ := filepath.Rel(tmpWorkDir, outsideTarget)
    w, _ := zw.Create(escapePath)
    _, _ = w.Write([]byte("pwned"))
    zw.Close()

    oldWd, _ := os.Getwd()
    os.Chdir(tmpWorkDir)
    defer os.Chdir(oldWd)

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)

    err = archives.ExtractZipArchive(zr)
    require.NoError(t, err)

    // Assert: file must NOT exist outside the extraction directory
    _, statErr := os.Stat(outsideTarget)
    assert.True(t, os.IsNotExist(statErr), "zip-slip: file was written outside extraction root at %s", outsideTarget)
}
```
Expected today: the assertion fails, i.e. `outsideTarget` exists, proving the zip-slip. After adding a containment check analogous to `tarzstd_extractor.go`, `ExtractZipArchive` should return an error (or skip the entry) and the file must not be created outside `tmpWorkDir`.

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

**File:** commands/helpers/archiver.go (L1-17)
```go
package helpers

import (
	"os"

	"gitlab.com/gitlab-org/gitlab-runner/commands/helpers/archive"
	"gitlab.com/gitlab-org/gitlab-runner/commands/helpers/archive/fastzip"
	"gitlab.com/gitlab-org/gitlab-runner/helpers/featureflags"

	// auto-register default archivers/extractors
	_ "gitlab.com/gitlab-org/gitlab-runner/commands/helpers/archive/gziplegacy"
	_ "gitlab.com/gitlab-org/gitlab-runner/commands/helpers/archive/raw"
	_ "gitlab.com/gitlab-org/gitlab-runner/commands/helpers/archive/tarzstd"
	_ "gitlab.com/gitlab-org/gitlab-runner/commands/helpers/archive/ziplegacy"

	"github.com/sirupsen/logrus"
)
```
