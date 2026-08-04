### Title
Zip Slip: unsanitized `file.Name` in `extractZipFile` allows path-traversal directory/file creation outside the job workspace with world-writable permissions - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipFile` calls `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and the subsequent entry writers use `file.Name` directly, with no check that the resolved path stays within the extraction root. Only a `.git`-directory check (`errorIfGitDirectory`) exists; there is no "zip slip" containment check comparable to the one present in the tar/zstd extractor, so a crafted `file.Name` containing `../` sequences can create directories and files outside the job workspace.

### Finding Description
`extractZipFile` (helpers/archives/zip_extract.go:61-83) does:
```go
err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
```
followed by `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, or `extractZipFileEntry`, all of which use `file.Name` (or `filepath.Dir(file.Name)`) verbatim with `os.Mkdir`, `os.OpenFile`, or `os.Symlink` [1](#0-0) . The only validation applied per entry in `ExtractZipArchive` is `errorIfGitDirectory(file.Name)`, which only rejects `.git`-prefixed paths and does nothing for `..` traversal [2](#0-1) [3](#0-2) .

This code is reached via `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`, whose `Extract` method simply does `archives.ExtractZipArchive(zr)` — notably, the `dir` field passed into the extractor (the intended extraction root) is stored but **never used or enforced** [4](#0-3) . This extractor is selected by the format dispatch table and invoked by, e.g., `CacheExtractorCommand.Execute`, which passes the job working directory (`wd`, from `os.Getwd()`) as the target directory but relies on the extractor to enforce containment [5](#0-4) .

By contrast, the tar/zstd extractor explicitly guards against this class of bug:
```go
path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))
if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir {
    return fmt.Errorf("%s cannot be extracted outside of chroot (%s)", path, e.dir)
}
``` [6](#0-5)  — no equivalent check exists in `helpers/archives/zip_extract.go` or in the ziplegacy code path. The `fastzip`-based extractor delegates path handling to the third-party `saracen/fastzip` library rather than this vulnerable code, so it is not affected by this specific bug [7](#0-6) .

Because the ziplegacy extractor operates on paths relative to the process current working directory (which is the job's build directory when the runner invokes the helper), a `file.Name` such as `../../../../tmp/evilworld/payload` will cause `filepath.Dir(file.Name)` to resolve outside the job workspace, and `os.MkdirAll(..., 0o777)` will create that directory chain (subject only to the umask and existing permissions) outside the intended root, followed by writing the file content into it via `extractZipFileEntry`.

### Impact Explanation
An attacker who controls the contents of a cache or artifact zip (a normal, unprivileged pipeline author) can cause the runner host process to create directories/files outside the job's build directory, with permissions up to `0o777` (world-writable, subject to umask) for the intermediate directories. On a runner host reused across jobs/users (shell or custom executor without container/namespace isolation — a permitted, non-privileged-admin scenario), this can pollute shared host paths (e.g., a shared `/tmp` subtree or a directory outside the workspace) with attacker-named, potentially executable content that a later job, cron task, or process running as a different user could pick up and execute, escalating from "job-scoped file write" to "host filesystem pollution that another job/user's process may later execute." This matches the scoped impact: persistent host filesystem pollution enabling later command execution by another job/user reusing the runner host.

### Likelihood Explanation
- Preconditions are realistic and attacker-controlled: any pipeline author can supply cache/artifact archives (via `cache:` config, `artifacts:` from a prior job, or a custom cache backend) whose zip entries are fully attacker-controlled, including `file.Name`.
- The exploit is reached deterministically whenever the ziplegacy zip extractor is selected (e.g., legacy cache/artifact format, or environments where `fastzip` isn't used) — no race conditions or timing requirements.
- No existing check in the call path (`ExtractZipArchive` → `extractZipFile`) validates `file.Name` against the extraction root; the only check present (`errorIfGitDirectory`) is unrelated.
- Repeatable: a fuzz test over `file.Name` values with `../` sequences under `ExtractZipArchive` would reliably produce writes/directories outside a temp extraction root.

### Recommendation
Before calling `os.MkdirAll`/`os.Mkdir`/`os.OpenFile`/`os.Symlink` on `file.Name`, resolve the target against the intended extraction root (the `dir` already threaded through `ziplegacy.extractor` but currently unused) and reject or skip entries whose cleaned, absolute path does not stay within that root — the same containment check already implemented in `tarzstd_extractor.go` (`filepath.Abs(filepath.Join(dir, name))` + `strings.HasPrefix` check) should be applied to `ExtractZipArchive`/`extractZipFile`. Additionally, avoid the blanket `0o777` permission on `MkdirAll` for created parent directories; use a restrictive default (e.g., `0o755` or narrower, consistent with masking against the process umask) and pass the actual extraction root all the way from `CacheExtractorCommand`/artifact download commands into `archives.ExtractZipArchive`.

### Proof of Concept
```go
package archives

import (
    "archive/zip"
    "os"
    "path/filepath"
    "testing"

    "github.com/stretchr/testify/require"
)

func TestExtractZipArchive_PathTraversalEscapesRoot(t *testing.T) {
    root := t.TempDir()
    outsideMarker := filepath.Join(filepath.Dir(root), "zipslip_poc_marker")
    defer os.RemoveAll(outsideMarker)

    origWd, _ := os.Getwd()
    require.NoError(t, os.Chdir(root))
    defer os.Chdir(origWd)

    tmpZip, _ := os.CreateTemp("", "poc*.zip")
    w := zip.NewWriter(tmpZip)
    f, _ := w.Create("../../zipslip_poc_marker/payload.sh")
    _, _ = f.Write([]byte("#!/bin/sh\necho pwned\n"))
    w.Close()
    tmpZip.Close()
    defer os.Remove(tmpZip.Name())

    err := ExtractZipFile(tmpZip.Name())
    require.NoError(t, err)

    // Assert the file was created OUTSIDE root — this is the bug.
    _, statErr := os.Stat(filepath.Join(outsideMarker, "payload.sh"))
    require.NoError(t, statErr, "expected escaped file to exist outside extraction root, proving zip-slip")
}
```
Expected result on the current code: the test passes, confirming the file/directory is created outside `root` (the intended extraction jail) — this is the vulnerability. After a fix that enforces containment (rejecting/normalizing `..`-escaping names against a real root), the same test should be updated to assert `os.IsNotExist(statErr)` is true, i.e., no file is created outside the root.

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

**File:** commands/helpers/archive/fastzip/zip_fastzip_extractor.go (L26-46)
```go
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
