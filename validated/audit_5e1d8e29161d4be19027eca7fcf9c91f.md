### Title
`ExtractZipFile`/`ExtractZipArchive` write entry paths verbatim with no root confinement, allowing zip-slip escape from artifact/cache extraction - (File: helpers/archives/zip_extract.go)

### Summary
`ExtractZipFile` → `ExtractZipArchive` → `extractZipFile` use `file.Name` directly for `os.MkdirAll`, `os.Mkdir`, `os.OpenFile`, and `os.Symlink` with no `..`-segment or absolute-path validation and no chroot/prefix check, unlike the sibling `tarzstd` extractor which explicitly resolves `filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects paths outside its root. A crafted zip archive consumed by this legacy zip extraction path can therefore write or symlink files outside the intended extraction root.

### Finding Description
`extractZipFile` (helpers/archives/zip_extract.go:61-83) calls `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and then, depending on entry type, `os.Mkdir(file.Name, ...)` (extractZipDirectoryEntry, line 13), `os.Symlink(string(data), file.Name)` (extractZipSymlinkEntry, line 37), or `os.OpenFile(file.Name, ...)` (extractZipFileEntry, line 51). None of these paths are canonicalized against, or checked to remain within, a caller-supplied root directory. The only validation performed in `ExtractZipArchive` (lines 88-96) is `errorIfGitDirectory`, which only blocks entries whose first path segment is literally `.git` (helpers/archives/path_check_helper.go:13-19) — it does not defend against `../` traversal, absolute paths, or symlink targets pointing outside the extraction root.

This stands in clear contrast to the `tarzstd` extractor (commands/helpers/archive/tarzstd/tarzstd_extractor.go:57-64), which resolves each entry against its `dir` root and explicitly rejects paths that don't have `dir` as a prefix ("cannot be extracted outside of chroot"). The legacy zip path has no equivalent check, and `ExtractZipArchive`/`ExtractZipFile` don't even take a `dir` parameter — extraction happens relative to whatever the process's current working directory is at call time, with entry names such as `../../secret`, `/etc/passwd`-style absolute paths (subject to how the zip writer encoded them), or symlink entries pointing outside the sandbox honored verbatim.

The exploit path is: an attacker (pipeline author) controls the contents of a cache or artifact archive (or any archive consumed through this code path, e.g. via `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`, which calls `archives.ExtractZipArchive` directly with no dir confinement) → crafts zip entries with `../` segments or malicious symlink targets → runner extracts the archive using this code → files land outside the intended extraction directory, in the checkout, cache, helper, or temp directory tree, depending on current working directory at extraction time.

### Impact Explanation
An unprivileged pipeline author who controls an artifact/cache archive consumed via this legacy zip extraction path can overwrite arbitrary files reachable from the extraction working directory (e.g. build checkout files, helper scripts, or other job state), potentially enabling execution-flow tampering in later job/pipeline stages or cross-job/cross-project state corruption if the extraction working directory is shared or predictable. This matches the scoped "protected-ref escalation or cross-job state tampering via path-root escape" impact.

### Likelihood Explanation
Preconditions are minimal: any user able to define a cache/artifact archive whose extraction routes through this zip code path (as opposed to fastzip, which is the modern default) can supply the crafted zip bytes. `zip.OpenReader`/`zip.NewReader` from Go's standard library do not sanitize `File.Name` — it is application code's responsibility, and this application code doesn't do it. The bug is deterministic and fully repeatable with a simple crafted archive; no race conditions or timing dependencies are involved.

### Recommendation
Change `ExtractZipArchive`/`ExtractZipFile` to accept a root/`dir` parameter (mirroring `tarzstd`'s extractor signature) and, for every entry, compute `target := filepath.Join(dir, file.Name)`, reject entries whose cleaned/absolute path does not have `dir` as a prefix (and reject/neutralize absolute paths and `..` segments before joining), and validate symlink targets similarly so they cannot resolve outside `dir`. Apply the same containment check consistently in `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, and `extractZipFileEntry`.

### Proof of Concept
Go unit test (mirroring `helpers/archives/zip_extract_test.go`'s pattern):
```go
func TestExtractZipFileTraversal(t *testing.T) {
    testOnArchive(t, func(t *testing.T, archive *zip.Writer) {
        f, err := archive.Create("../evil_outside_root.txt")
        require.NoError(t, err)
        _, err = io.WriteString(f, "pwned")
        require.NoError(t, err)
    }, func(t *testing.T, fileName string) {
        wd, _ := os.Getwd()
        parentEvilPath := filepath.Join(filepath.Dir(wd), "evil_outside_root.txt")
        defer os.Remove(parentEvilPath)

        err := ExtractZipFile(fileName)
        require.NoError(t, err)

        // Assertion: file must NOT exist outside the extraction root.
        _, statErr := os.Stat(parentEvilPath)
        assert.True(t, os.IsNotExist(statErr), "zip-slip: file was written outside extraction root")
    })
}
```
Expected current behavior: the file is created at `parentEvilPath` (outside root), demonstrating the escape; after a fix, `os.Stat` should return `IsNotExist`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** helpers/archives/zip_extract.go (L12-39)
```go
func extractZipDirectoryEntry(file *zip.File) (err error) {
	err = os.Mkdir(file.Name, file.Mode().Perm())

	// The "directory does exist" error is not an error for us
	if os.IsExist(err) {
		err = nil
	}
	return
}

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

**File:** helpers/archives/zip_extract.go (L85-120)
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

func ExtractZipFile(fileName string) error {
	archive, err := zip.OpenReader(fileName)
	if err != nil {
		return err
	}
	defer func() { _ = archive.Close() }()

	return ExtractZipArchive(&archive.Reader)
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L1-33)
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
}
```
