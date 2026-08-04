## Title
`ExtractZipFile`/`ExtractZipArchive` performs no root-confinement path validation at all, allowing zip-slip path traversal - (File: helpers/archives/zip_extract.go)

## Summary
`extractZipFile` in `helpers/archives/zip_extract.go` writes each zip entry using `file.Name` directly via `os.Mkdir`/`os.OpenFile`/`os.Symlink`, with the only pre-write check being a `.git`-directory warning. Unlike the sibling tar+zstd extractor, which explicitly joins the entry name to a target directory and verifies the resolved absolute path is still prefixed by that directory, the zip path performs no such validation, so any entry name containing `..` segments (with either `/` or `\`) can write outside the intended extraction root.

## Finding Description
`ExtractZipFile` opens the archive and calls `ExtractZipArchive`, which iterates `archive.File` and for each entry calls `errorIfGitDirectory(file.Name)` and then `extractZipFile(file)`: [1](#0-0) 

`errorIfGitDirectory`/`isPathAGitDirectory` only checks whether `filepath.Clean(path)`'s first segment is `.git` — it is not a path-confinement check, it's a git-directory content warning: [2](#0-1) 

`extractZipFile` then does:
```go
err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
...
extractZipDirectoryEntry / extractZipSymlinkEntry / extractZipFileEntry
```
all of which use `file.Name` verbatim as the OS path (`os.Mkdir(file.Name, ...)`, `os.OpenFile(file.Name, ...)`, `os.Symlink(string(data), file.Name)`): [3](#0-2) 

There is no `filepath.Abs`/`filepath.Join(root, name)` + `strings.HasPrefix(path, root)` check anywhere in this file, and no call to `filepath.Rel` or `IsLocal`. Contrast this with the tar+zstd extractor used for cache archives, which explicitly resolves and validates every entry against `e.dir` before any filesystem operation: [4](#0-3) 

The zip legacy extractor (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`) simply forwards to `archives.ExtractZipArchive` without adding any additional confinement check of its own: [5](#0-4) 

Go's standard `archive/zip` package does **not** sanitize `FileHeader.Name`/`File.Name` against `..` traversal or absolute paths on read — it is a plain string field populated from the raw zip central directory bytes, and the caller is responsible for path validation. Because `extractZipFile` operates relative to the current working directory (there is no `dir` parameter at all — this extractor is always invoked from within the already-`os.Chdir`'d extraction directory), any entry name such as `../../etc/cron.d/evil`, or on Windows `..\\..\\Users\\Public\\evil.bat`, or a mix of both separators (`..\\../evil`), is passed straight to `os.MkdirAll`/`os.OpenFile` and will be created relative to that directory, i.e., outside the intended root. Because there is no canonicalization/validation step whatsoever (not merely inconsistent separator handling between a validation phase and a write phase — there is simply no validation phase), any `..`-containing name, regardless of separator style, escapes.

## Impact Explanation
A CI job (or a pipeline consuming a cache/artifact produced by an attacker-controlled job, e.g. through `dependencies:` on another job's artifacts, or a shared/poisoned cache key) can craft a ZIP artifact/cache archive whose entries contain `../` (or backslash) traversal sequences. When the runner extracts it via `ExtractZipFile`/`ExtractZipArchive` (used by the legacy zip extractor path for cache/artifact restoration), files/symlinks are written or overwritten outside the build directory, anywhere the runner process has filesystem permissions. This can overwrite runner-managed files, other projects' build directories (on shared runners without full isolation), or files on the host in shell-executor configurations, matching the "path-root escape leading to stronger-context overwrite" impact.

## Likelihood Explanation
This is highly feasible and repeatable: the attacker only needs to control an artifact or cache zip that another job on the same runner extracts (e.g., by controlling a cache key/artifact producing job, or publishing artifacts consumed via `dependencies`). No special privileges are needed beyond normal `.gitlab-ci.yml` control over one job. The exploit is deterministic — any `..`-containing zip entry name triggers it, since there is no mitigation code path at all in `zip_extract.go`.

## Recommendation
Add explicit root-confinement validation in `extractZipFile` (or in `ExtractZipArchive`) mirroring the tar+zstd extractor: resolve each `file.Name` against the intended extraction root using `filepath.Join` + `filepath.Abs`, canonicalize both `\` and `/` via `filepath.FromSlash`/`ToSlash` consistently, and reject (or skip with a warning) any entry whose resolved path is not prefixed by the root (using `filepath.Rel` and checking for a leading `..` component, not string prefix, to avoid separator/casing edge cases). Reject absolute paths and entries with a Windows drive letter or UNC prefix even on non-Windows builds, since archives may be extracted cross-platform.

## Proof of Concept
```go
// helpers/archives/zip_extract_traversal_test.go
package archives

import (
	"archive/zip"
	"io"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/require"
)

func TestExtractZipFile_PathTraversal(t *testing.T) {
	dir := t.TempDir()
	wd, _ := os.Getwd()
	require.NoError(t, os.Chdir(dir))
	defer os.Chdir(wd)

	tempFile, err := os.CreateTemp("", "archive")
	require.NoError(t, err)
	defer os.Remove(tempFile.Name())

	w := zip.NewWriter(tempFile)
	// mixed-separator traversal entry
	f, err := w.Create("../evil_outside_root.txt")
	require.NoError(t, err)
	_, _ = io.WriteString(f, "pwned")
	require.NoError(t, w.Close())
	tempFile.Close()

	err = ExtractZipFile(tempFile.Name())
	require.NoError(t, err)

	// Assert file did NOT escape the extraction root
	_, statErr := os.Stat(filepath.Join(dir, "..", "evil_outside_root.txt"))
	require.True(t, os.IsNotExist(statErr), "zip entry escaped extraction root")
}
```
Expected current behavior: the test fails because `evil_outside_root.txt` is created one directory above the intended extraction root, confirming the missing root-confinement check.

### Citations

**File:** helpers/archives/zip_extract.go (L12-59)
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
