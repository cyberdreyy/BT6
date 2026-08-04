### Title
Second metadata-restoration pass in `ExtractZipArchive` follows attacker-planted symlinks, letting `lchmod`/`processZipExtra` write permissions/timestamps outside the job root - (File: `helpers/archives/zip_extract.go`)

### Summary
`ExtractZipArchive` extracts all zip entries in a first pass, then does a second pass over the *same raw* `archive.File` slice calling `lchmod(file.Name, file.Mode())` and `processZipExtra(&file.FileHeader)` for every entry, including duplicate-named ones. Because the second pass never re-`lstat`s the path or de-duplicates by name, a crafted archive can leave a symlink on disk at a given path while a *different* entry sharing that name (processed later in the second loop) carries a "regular file" `Mode()`, causing `lchmod`/`os.Chtimes` to dereference the symlink and mutate metadata at the attacker-chosen target.

### Finding Description
`ExtractZipArchive` in [1](#0-0)  runs two loops over `archive.File`. The first loop calls `extractZipFile`, which for symlink entries calls `extractZipSymlinkEntry` — this removes any existing path and creates `os.Symlink(string(data), file.Name)` with **no validation of the link target** [2](#0-1) . Regular-file entries similarly `os.Remove(file.Name)` then recreate the path as a real file [3](#0-2) . Because both entry types remove-then-recreate the *same path* string, an attacker can put two entries with the same `Name` in the archive (one regular, one symlink) so that after the first loop, the on-disk object's actual type (say, a symlink to `/etc/shared/file` or another path outside the job root) does **not** match the `Mode()` carried by every archive entry that shares that name.

The second loop then iterates the raw `archive.File` slice again — not a de-duplicated final-state view — and for the entry whose header says "regular file" (even though the winning on-disk object is actually the attacker's symlink), it calls:
- `lchmod(file.Name, file.Mode())`: on Linux, `os_unix.go`'s `lchmod` only skips when the *current entry's* `mode` has the symlink bit set; for a "regular" mode it calls `unix.Fchmodat(unix.AT_FDCWD, name, ..., flags=0)`, i.e. with no `AT_SYMLINK_NOFOLLOW`, meaning it **follows** the symlink now sitting at that path [4](#0-3) .
- `processZipExtra` → `processZipTimestampField`: it only skips for entries whose *own* `Mode()` is neither dir nor regular [5](#0-4) ; since this specific entry says "regular", it proceeds to `os.Chtimes(file.Name, ...)`, and Go's `os.Chtimes` has no `l`-variant — it always follows symlinks.

No code re-validates via `os.Lstat` that `file.Name` still refers to the type of object the header claims, and there is no restriction on symlink target destinations (`extractZipSymlinkEntry`) or on `file.Name` itself. The `pathErrorTracker` only suppresses duplicate warning logs, it performs no security check [6](#0-5) . The only existing safeguard (`errorIfGitDirectory`) targets `.git` paths, not symlink/path escapes [7](#0-6) .

### Impact Explanation
An unprivileged pipeline author who controls artifact/cache zip contents can cause the Runner process (shell/custom executors, or any executor where extraction happens on a shared host/cache volume) to `chmod`/`chtimes` an arbitrary followed path outside the extraction root, as long as they can predict or reference an existing path (absolute symlink target, or relative traversal via `..`). This is metadata/permission corruption of files the runner process can write to — potentially other jobs' cached files, shared cache storage, or other host paths reachable from the runner's file permissions — persisting across job boundaries.

### Likelihood Explanation
Requires only crafting a cache/artifact zip with two entries sharing the same `Name` (one symlink entry ordered so it is applied last in the first pass, one regular-mode entry with a `Timestamp` extra field, in any relative order in `archive.File` since duplicate names are processed independently in both passes) — fully attacker-controlled via `.gitlab-ci.yml` cache/artifact contents, no special privilege needed. This is deterministic given zip/archive.File ordering the attacker fully controls when building the zip.

### Recommendation
In the second loop of `ExtractZipArchive`, `Lstat` `file.Name` before calling `lchmod`/`processZipExtra` and skip (or fail) if the actual filesystem entry type doesn't match `file.Mode()&os.ModeType`, or better, deduplicate entries by name and only process metadata for the final on-disk object using its real `Lstat` result rather than each raw header's claimed mode. Additionally, validate symlink targets in `extractZipSymlinkEntry` stay within the extraction root.

### Proof of Concept
```go
func TestExtractZipArchive_DuplicateNameSymlinkMetadataEscape(t *testing.T) {
    dir := t.TempDir()
    outside := t.TempDir()
    targetFile := filepath.Join(outside, "victim")
    require.NoError(t, os.WriteFile(targetFile, []byte("x"), 0o644))

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)

    // Entry 1: regular file "foo" with a timestamp extra field
    w1, _ := zw.CreateHeader(&zip.FileHeader{Name: "foo", Method: zip.Store})
    w1.Write([]byte("data"))

    // Entry 2: symlink "foo" -> outside target (processed last in extraction loop 1)
    w2, _ := zw.CreateHeader(&zip.FileHeader{Name: "foo", Method: zip.Store})
    w2.Write([]byte(targetFile))
    // manually mark entry2 as symlink mode after creating header, or use SetMode(os.ModeSymlink)

    zw.Close()

    r, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    os.Chdir(dir)
    beforeInfo, _ := os.Lstat(targetFile)

    err := ExtractZipArchive(r)
    require.NoError(t, err)

    afterInfo, _ := os.Lstat(targetFile)
    // Assert: victim file outside job root must be untouched
    assert.Equal(t, beforeInfo.ModTime(), afterInfo.ModTime())
    assert.Equal(t, beforeInfo.Mode(), afterInfo.Mode())
}
```
Expected (buggy) result: `targetFile`'s mtime/mode changes because the "regular" entry's `lchmod`/`processZipExtra` follow the symlink left by the "symlink" entry sharing the same `Name`.

### Citations

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

**File:** helpers/archives/zip_extract.go (L41-59)
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
```

**File:** helpers/archives/zip_extract.go (L85-107)
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
```

**File:** helpers/archives/os_unix.go (L12-29)
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

**File:** helpers/archives/path_error_tracker.go (L17-35)
```go
func (p *pathErrorTracker) actionable(e error) bool {
	pathErr, isPathErr := e.(*os.PathError)
	if e == nil || isPathErr && pathErr == nil {
		return false
	}

	if !isPathErr {
		return true
	}

	p.lock.Lock()
	defer p.lock.Unlock()

	seen := p.seenOps[pathErr.Op]
	p.seenOps[pathErr.Op] = true

	// actionable if *not* seen before
	return !seen
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
