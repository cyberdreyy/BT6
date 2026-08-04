### Title
Zip-slip path traversal in `extractZipFile` allows extraction outside job workspace root - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipFile` never sanitizes `file.Name` before using it in `filepath.Dir(file.Name)` for `os.MkdirAll`, nor in `os.OpenFile`/`os.Symlink` for the actual entry write, and the only pre-check performed (`errorIfGitDirectory`) only rejects `.git` prefixed paths, not `..` traversal. A crafted cache/artifact zip entry name such as `../../shared/evil.txt` will therefore create files/symlinks and later run `lchmod` outside the intended extraction directory.

### Finding Description
`ExtractZipArchive` iterates `archive.File` and, for each entry, only checks `errorIfGitDirectory(file.Name)` [1](#0-0)  — a check that only rejects paths whose first cleaned segment is `.git` [2](#0-1) . It does not clean, reject, or confine `..` segments, absolute paths, or symlink escapes.

`extractZipFile` then calls `os.MkdirAll(filepath.Dir(file.Name), 0o777)` directly on the raw, attacker-controlled name [3](#0-2) , followed by `extractZipFileEntry`/`extractZipSymlinkEntry`, which call `os.OpenFile(file.Name, ...)` and `os.Symlink(string(data), file.Name)` respectively, again on the unsanitized name [4](#0-3) . None of these resolve or clamp the path against an extraction root — there is no `filepath.Join(root, file.Name)` + prefix-check pattern anywhere in this file.

After all files are processed, `ExtractZipArchive` runs a second pass calling `lchmod(file.Name, file.Mode())` again using the same unsanitized name [5](#0-4) . On Windows, `lchmod` calls `os.Chmod(name, mode.Perm())` directly on that path [6](#0-5) ; on Unix it calls `unix.Fchmodat(unix.AT_FDCWD, name, ...)` [7](#0-6) . Both resolve relative to the process's current working directory, so if extraction escaped the root during the write phase, the chmod phase follows the same escaped path and mutates permissions on the out-of-root file too.

The caller `ziplegacy.extractor.Extract` passes the `zip.Reader` straight to `archives.ExtractZipArchive` with no path validation or root confinement of its own [8](#0-7) ; extraction is confined only implicitly by the process's current working directory at invocation time, which the runner sets before invoking the helper — but nothing in this code path re-validates that each entry's resolved path stays under that directory.

### Impact Explanation
An unprivileged pipeline author who controls a cache key or job artifact can embed zip entries with `../` traversal segments in their names. When that cache/artifact is restored, `extractZipFile` will create parent directories and write files (or symlinks) at attacker-chosen absolute/relative paths outside the intended job workspace, and the subsequent `lchmod` pass will additionally change permissions on those out-of-root paths. On hosts where the runner's working directory or extraction root is shared/reused across projects (e.g., shared runner cache directory structure, or predictable build paths), this enables cross-project file write/overwrite and permission tampering outside the job sandbox — matching the scoped impact of the question.

### Likelihood Explanation
Feasibility is high and fully attacker-controlled: cache and artifact zip contents (including entry names) are supplied by the job/pipeline author with no server-side re-validation of `zip.File.Name` beyond the `.git`-prefix check. Exploitation requires only that the extraction root be reachable/predictable relative to other jobs' data on the same host (a documented precondition, not an admin misconfiguration) — this is a standard "zip-slip" class vulnerability, well-known and trivially reproducible with a crafted zip.

### Recommendation
Sanitize every `file.Name` before use: resolve it against the intended extraction root with `filepath.Join(root, file.Name)`, then verify the result is still contained within `root` (e.g., via `filepath.Rel` and rejecting results starting with `..`, or comparing cleaned absolute paths) in `extractZipFile`, `extractZipFileEntry`, `extractZipSymlinkEntry`, and again before the `lchmod` call in `ExtractZipArchive`. Reject or skip entries whose names contain `..` segments, are absolute, or whose symlink targets attempt to escape the root, consistent with the existing `errorIfGitDirectory` guard pattern.

### Proof of Concept
Go unit test in `helpers/archives/zip_extract_test.go`:
1. Create a temp directory `root` and `chdir` into it (or pass it as extraction root if refactored).
2. Build an in-memory zip via `archive/zip` containing one entry named `../outside/evil.txt` with arbitrary content and a benign mode.
3. Call `archives.ExtractZipArchive` on the reader.
4. Assert: `filepath.Join(root, "..", "outside", "evil.txt")` does NOT exist, and no file/symlink was created outside `root`. Currently the file is created and `lchmod` succeeds on it, demonstrating the escape — the test should fail against current code, proving the vulnerability, and pass once a root-containment check is added.

### Citations

**File:** helpers/archives/zip_extract.go (L22-58)
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
```

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

**File:** helpers/archives/os_windows.go (L9-13)
```go
func lchmod(name string, mode os.FileMode) error {
	if mode&os.ModeSymlink != 0 {
		return nil
	}
	return os.Chmod(name, mode.Perm())
```

**File:** helpers/archives/os_unix.go (L12-28)
```go
func lchmod(name string, mode os.FileMode) error {
	var flags int

	if runtime.GOOS == "linux" {
		// Linux does not support changing modes on symlinks.
		if mode&os.ModeSymlink != 0 {
			return nil
		}
	} else {
		flags = unix.AT_SYMLINK_NOFOLLOW
	}

	err := unix.Fchmodat(unix.AT_FDCWD, name, uint32(mode.Perm()), flags)
	if err != nil {
		return &os.PathError{Op: "lchmod", Path: name, Err: err}
	}
	return nil
```
