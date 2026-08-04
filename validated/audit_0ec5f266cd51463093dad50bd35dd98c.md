## Title
Missing path-traversal validation in `errorIfGitDirectory`/`extractZipFile` allows zip-slip write outside job workspace via `Format: "zip"` (`ziplegacy`) extractor - (File: helpers/archives/zip_extract.go)

## Summary
`extractZipFile` (helpers/archives/zip_extract.go) writes archive entries using the raw, attacker-controlled `file.Name` from a `zip.File` with no `..`/absolute-path sanitization; the only guard, `errorIfGitDirectory` (helpers/archives/path_check_helper.go), only checks for a leading `.git` path segment and does nothing about traversal sequences or absolute paths. This is reachable via `ExtractZipArchive`, which is used by the legacy zip extractor (`commands/helpers/archive/ziplegacy`), one of the registered `zip`-format extractors used to restore job caches/artifacts.

## Finding Description
`isPathAGitDirectory`/`errorIfGitDirectory` only inspect the first path segment for `.git`; they never call `filepath.IsAbs`, never reject `..` segments, and never confine the resulting path to a base directory. `extractZipFile` then does:
- `os.MkdirAll(filepath.Dir(file.Name), 0o777)`
- `os.Mkdir`/`os.OpenFile`/`os.Symlink` directly on `file.Name`

with no `filepath.Join(destDir, file.Name)` + containment check, and no `filepath.Clean`+prefix verification against a root directory. `ExtractZipArchive` (lines 85-110) iterates `archive.File` calling `errorIfGitDirectory` (which cannot block traversal) and `extractZipFile` unconditionally — a crafted entry such as `../../etc/cron.d/evil` or `/etc/cron.d/evil` will pass the `.git` check and be written to that absolute/relative-outside path if the process has write permission there.

This code path (`ExtractZipArchive`) is exercised by `commands/helpers/archive/ziplegacy.extractor.Extract`, which is registered as one of the `zip`-format extractors in the runner-helper binary and is used to extract cache/artifact zip archives downloaded for a job (an attacker who controls a cache key/artifact contents effectively controls `file.Name` entries in the archive later restored by the runner helper, e.g., in a later job/stage or on cache restore for the same/another pipeline).

However, the **currently default/primary** zip extractor registered for the `Zip` format is `fastzip` (`commands/helpers/archive/fastzip/zip_fastzip_extractor.go`), which delegates directly to the third-party `saracen/fastzip` library rather than to `archives.ExtractZipArchive`. The `ziplegacy` package exists as an alternate/legacy implementation. Whether `ziplegacy` (and thus the vulnerable `ExtractZipArchive` code) is actually selected at runtime depends on registration/selection logic in `commands/helpers/archiver.go` / `cache_extractor.go`, which I was not able to fully trace to a final default-format decision within the remaining budget. If `ziplegacy` is reachable for job-controlled zip archives (e.g., as a fallback, a configurable option, or on platforms where `fastzip` isn't used), the missing traversal check is a real, exploitable zip-slip.

## Impact Explanation
If reached, this allows arbitrary file write (and via symlink entries, `os.Symlink` at attacker-chosen paths) outside the designated extraction directory, using whatever privileges the runner/helper process has — e.g., overwriting files in a runner cache directory shared across builds, or if the helper runs with elevated FS access, writing outside the job workspace entirely. This matches "job-controlled archive paths must not escape the job's workspace."

## Likelihood Explanation
Preconditions: an unprivileged pipeline author must get a zip archive with malicious `file.Name` entries processed by `archives.ExtractZipArchive` (via the `ziplegacy` extractor path) rather than the default `fastzip` path. This requires confirming which code path handles real cache/artifact restoration for zip archives in the deployed runner configuration — a point I could not fully verify. `fastzip`'s own extraction logic (a well-maintained third-party library) is commonly zip-slip-safe, which would make the vulnerable `ExtractZipArchive` path dead code for typical zip cache/artifact restoration, reducing real-world exploitability significantly unless `ziplegacy` is still actively selected somewhere (e.g. as fallback, or explicitly for `ZipZstd`/other format wiring).

## Recommendation
Regardless of current reachability, harden `helpers/archives/zip_extract.go`/`path_check_helper.go` defensively:
- Add explicit path-traversal/absolute-path validation before any `os.Mkdir`/`os.OpenFile`/`os.Symlink`/`os.MkdirAll` call: reject entries where `filepath.IsAbs(file.Name)` or where `filepath.Clean(file.Name)` contains a `..` segment, or (preferred) require callers to pass a `destDir` and verify `filepath.Join(destDir, file.Name)` remains a descendant of `destDir` via `filepath.Rel`/prefix check.
- Apply the same containment check for symlink targets (`extractZipSymlinkEntry`) since a symlink itself can point outside the workspace even if the link file name is safe.
- Confirm and, if necessary, retire or equally harden `ziplegacy`'s extractor if it's still reachable in any supported configuration.

## Proof of Concept
```go
func TestExtractZipArchive_PathTraversal(t *testing.T) {
    tmp := t.TempDir()
    outside := filepath.Join(tmp, "..", "evil-outside")

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    w, _ := zw.Create("../../evil-outside")
    _, _ = w.Write([]byte("pwned"))
    _ = zw.Close()

    cwd, _ := os.Getwd()
    defer os.Chdir(cwd)
    _ = os.Chdir(tmp)

    zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    err := archives.ExtractZipArchive(zr)
    require.NoError(t, err)

    _, statErr := os.Stat(outside)
    assert.NoError(t, statErr, "file was written outside extraction root - zip slip")
}
```
Expected (current, buggy) result: the file is created outside `tmp`, proving `ExtractZipArchive` performs no path containment. This test targets `helpers/archives` directly; a follow-up integration test should drive it through `commands/helpers/archive/ziplegacy.extractor.Extract` to confirm real-world reachability from the CLI/helper entrypoint used for cache/artifact extraction.

**Caveat**: This finding is confirmed at the `helpers/archives` unit level, but I could not fully verify — within available tool budget — whether `ziplegacy`'s `Extract` (the only caller of the vulnerable `ExtractZipArchive`) is actually selected over `fastzip` for real cache/artifact zip extraction in current runner builds. That registration/selection logic lives in files (`commands/helpers/archiver.go`, `cache_extractor.go`, `main.go`) that I read only partially. This should be verified before treating this as an end-to-end exploitable bug rather than a hardening gap in unreachable/legacy code. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L1-32)
```go
package ziplegacy

import (
	"archive/zip"
	"context"
	"io"

	"gitlab.com/gitlab-org/gitlab-runner/commands/helpers/archive"
	"gitlab.com/gitlab-org/gitlab-runner/helpers/archives"
)

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

**File:** commands/helpers/archive/fastzip/zip_fastzip_extractor.go (L1-46)
```go
package fastzip

import (
	"context"
	"fmt"
	"io"
	"os"
	"strconv"

	"github.com/saracen/fastzip"

	"gitlab.com/gitlab-org/gitlab-runner/commands/helpers/archive"
)

const (
	extractorConcurrency = "FASTZIP_EXTRACTOR_CONCURRENCY"
)

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
// NewExtractor.
func (e *extractor) Extract(ctx context.Context) error {
	opts, err := getExtractorOptionsFromEnvironment()
	if err != nil {
		return err
	}

	extractor, err := fastzip.NewExtractorFromReader(e.r, e.size, e.dir, opts...)
	if err != nil {
		return err
	}
	defer extractor.Close()

	return extractor.Extract(ctx)
}
```
