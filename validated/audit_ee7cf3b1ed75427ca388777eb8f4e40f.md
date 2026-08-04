### Title
Zip extraction has no path-containment check, letting a crafted entry name make `lchmod` and `os.Chtimes` (via `processZipTimestampField`) act on arbitrary paths outside the workspace - ([File: helpers/archives/zip_extract.go], [File: helpers/archives/zip_extra.go])

### Summary
`ExtractZipArchive` passes the raw, attacker-controlled `zip.File.Name` unchanged to `lchmod` and to `processZipTimestampField` (which calls `os.Chtimes(file.Name, ...)`), with no `filepath.Clean`/containment check anywhere in the extraction path. A zip entry name containing `../` segments therefore causes both metadata primitives to target the same traversal-resolved path, and — because the same unsanitized name is also used earlier for `os.Mkdir`, `os.OpenFile`, and `os.Symlink` in `extractZipFileEntry`/`extractZipSymlinkEntry` — content, permissions, and timestamps of a file outside the intended extraction root can all be attacker-influenced together.

### Finding Description
`ExtractZipArchive` in [1](#0-0)  iterates `archive.File` twice: first calling `extractZipFile(file)` (which does `os.Mkdir`/`os.OpenFile`/`os.Symlink` directly on `file.Name`), then calling `lchmod(file.Name, file.Mode())` and `processZipExtra(&file.FileHeader)`. `processZipExtra` dispatches to `processZipTimestampField`, which calls `os.Chtimes(file.Name, acTime, modTime)` [2](#0-1) . `lchmod` on Unix calls `unix.Fchmodat(unix.AT_FDCWD, name, ...)` and on Windows calls `os.Chmod(name, ...)`, both using the identical `file.Name` string [3](#0-2) [4](#0-3) .

There is no call to `filepath.Clean`, no check that the resolved path stays under a base extraction directory, and no rejection of `..` components anywhere in `zip_extract.go` or `zip_extra.go`. The only unrelated validation present is `errorIfGitDirectory`, which only warns about `.git` directories and does not block execution [5](#0-4) . Consequently, a zip entry named e.g. `../../../../home/gitlab-runner/scripts/build.sh` is used verbatim by `extractZipFileEntry` (content write), `lchmod` (permission change), and `os.Chtimes` via `processZipTimestampField` (mtime/atime reset) — all three primitives resolve to the exact same out-of-workspace path with zero containment checks between them.

### Impact Explanation
An attacker who can get the runner to extract a crafted zip archive (e.g., a job/cache/artifact archive under their control) can write, chmod, and reset the mtime of files outside the extraction root in one shot. Concretely, this allows: (1) overwriting arbitrary files reachable by the runner process's filesystem permissions, since `extractZipFileEntry` already performs unrestricted `os.OpenFile`/`io.Copy` on the same unsanitized name; and (2) once a file is written/exists, resetting its `mtime` via `Chtimes` and its permission bits via `lchmod` to defeat any staleness/permission-based gating logic that inspects those attributes (e.g., "only re-run script if mtime changed" or executable-bit checks) on the runner host, outside the job's sandbox.

### Likelihood Explanation
Feasibility depends on the runner process's OS-level write permissions to the target path and on an attacker being able to supply a zip archive whose entry names it fully controls (e.g., via a job-defined cache/artifact archive that gets restored/extracted by the runner using `ExtractZipArchive`/`ExtractZipFile`). Since normal CI job authors can shape cache/artifact contents, and the extraction code performs no path sanitization at any point in the two-pass loop, this is reliably reproducible whenever such an extraction path is reachable with attacker-supplied zip bytes.

### Recommendation
Validate and normalize every `file.Name` once, immediately after opening the zip archive, before any filesystem operation (Mkdir/OpenFile/Symlink/lchmod/Chtimes): resolve it with `filepath.Join(destRoot, filepath.Clean(file.Name))`, then verify with `filepath.Rel`/`strings.HasPrefix` that the resolved path stays under `destRoot`; reject (skip with a warning, similar to `errorIfGitDirectory`) any entry that fails this containment check. Apply this single sanitized path consistently to `extractZipFile`, `lchmod`, and `processZipExtra`/`processZipTimestampField` so all three operations are guaranteed to target the same validated, contained path.

### Proof of Concept
Go unit test in `helpers/archives`:
```go
func TestZipSlip_LchmodAndChtimesFollowTraversal(t *testing.T) {
    tmp := t.TempDir()
    outsideDir := t.TempDir() // simulate "outside workspace"
    traversalTarget := filepath.Join(outsideDir, "pwned.sh")

    // relative traversal name as would appear in file.Name after entry.Name is used unsanitized
    entryName, _ := filepath.Rel(tmp, traversalTarget)

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    fh := &zip.FileHeader{Name: entryName, Method: zip.Deflate}
    fh.SetMode(0644)
    w, _ := zw.CreateHeader(fh)
    _, _ = w.Write([]byte("echo pwned"))
    _ = zw.Close()

    // extract while cwd = tmp, simulating workspace root
    os.Chdir(tmp)
    zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    _ = ExtractZipArchive(zr)

    // Assert file landed outside tmp, and both lchmod and Chtimes acted on it
    info, err := os.Stat(traversalTarget)
    require.NoError(t, err) // proves write escaped workspace
    require.Equal(t, os.FileMode(0644), info.Mode().Perm()) // lchmod applied outside root
    // mtime should be close to "now" if timestamp field flag was set, proving Chtimes also targeted same path
}
```
Expected assertions: the file is created at `traversalTarget` (outside `tmp`), `lchmod`/`os.Chtimes` both succeed against that same escaped path, and no error/warning indicates containment enforcement — confirming both primitives operate identically on the traversal-resolved path with no path-containment check between them.

### Citations

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

**File:** helpers/archives/zip_extra.go (L50-68)
```go
func processZipTimestampField(data []byte, file *zip.FileHeader) error {
	if !file.Mode().IsDir() && !file.Mode().IsRegular() {
		return nil
	}

	var tsField ZipTimestampField
	err := binary.Read(bytes.NewReader(data), binary.LittleEndian, &tsField)
	if err != nil {
		return err
	}

	if (tsField.Flags & 1) == 1 {
		modTime := time.Unix(int64(tsField.ModTime), 0)
		acTime := time.Now()
		return os.Chtimes(file.Name, acTime, modTime)
	}

	return nil
}
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

**File:** helpers/archives/os_windows.go (L9-13)
```go
func lchmod(name string, mode os.FileMode) error {
	if mode&os.ModeSymlink != 0 {
		return nil
	}
	return os.Chmod(name, mode.Perm())
```
