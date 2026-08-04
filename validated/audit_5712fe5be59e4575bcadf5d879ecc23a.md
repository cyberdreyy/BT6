### Title
Zip entry-order TOCTOU allows a duplicate-name symlink entry to redirect the second-pass `os.Chtimes` metadata call to an out-of-tree symlink target - ([File: helpers/archives/zip_extra.go])

### Summary
`ExtractZipArchive` in `helpers/archives/zip_extract.go` extracts every archive entry in a first pass and then, in a second pass, applies `lchmod` and `processZipExtra` (Chtimes/Lchown) using each entry's original `*zip.FileHeader`, not the file type currently present on disk at `file.Name`. When two entries share the same `Name` — an earlier regular file and a later symlink — the second pass still evaluates the *earlier* (regular-file) header against the path, and `os.Chtimes` (which follows symlinks) is executed against whatever now occupies that path, i.e. the symlink installed by the later entry.

### Finding Description
- First loop, `ExtractZipArchive` (`helpers/archives/zip_extract.go:88-96`): iterates `archive.File` in archive order and calls `extractZipFile`, which for entry A (regular file at path `P`) creates a regular file at `P` via `extractZipFileEntry`, and for entry B (symlink, same `Name` `P`) later calls `extractZipSymlinkEntry`, which does `os.Remove(file.Name)` then `os.Symlink(target, file.Name)` — replacing `P` with a symlink pointing to an attacker-chosen `target` (`helpers/archives/zip_extract.go:22-39`). Nothing prevents duplicate `Name` values or validates that a later entry doesn't clobber an earlier one's path with a different file type.
- Second loop (`helpers/archives/zip_extract.go:98-107`): for each `file` in `archive.File` (including entry A), it calls `lchmod(file.Name, file.Mode())` and `processZipExtra(&file.FileHeader)`. Both operate using entry A's header/mode (regular file), but on disk `P` is now a symlink from entry B.
- `processZipExtra` → `processZipTimestampField` (`helpers/archives/zip_extra.go:50-68`) gates on `file.Mode().IsDir() || file.Mode().IsRegular()`; entry A's header says "regular," so the gate passes, and `os.Chtimes(file.Name, acTime, modTime)` executes. `os.Chtimes` follows symlinks, so it applies the timestamp to whatever `P` (now a symlink) resolves to — which can be outside the extraction root if the attacker supplies an absolute path or a `../` relative target in entry B's symlink payload.
- By contrast, `lchmod` and `os.Lchown` (used for the UID/GID extra field, `helpers/archives/zip_extra_unix.go:37-48`) operate on the link itself and are not affected by this reordering trick.
- The existing safety check in `processZipTimestampField` (mode must be dir/regular) is meant to stop metadata operations from being applied through a symlink, but it is defeated because the check uses the *header's* mode for entry A rather than the *actual current file type at `file.Name`* at the time the second pass runs.

### Impact Explanation
An attacker who supplies an artifact/cache zip (or any archive extracted via `ExtractZipArchive`, e.g. `ExtractZipFile`) can craft two entries with the same `Name`: entry A a regular file, entry B a symlink to an arbitrary path (e.g., `/etc/cron.d/foo`, or a path outside the job's working directory but writable by the runner user). After extraction, `os.Chtimes` will follow the symlink and modify the modification/access timestamp of the target file. This is a confined but real out-of-tree filesystem side effect (timestamp corruption via symlink following), not content corruption or privilege escalation — `Lchown`/`lchmod` are safe because they use non-following link operations.

### Likelihood Explanation
Fully attacker-controlled: any job author who can supply a zip artifact/cache that Runner extracts (e.g., via `artifacts:` download or cache restore) controls entry order, duplicate names, and symlink targets. No special privileges are required; only that the runner process has write access to the symlink target. Reproducible deterministically since entry order in `archive.File` is fixed by the zip's own byte layout.

### Recommendation
In the second pass of `ExtractZipArchive`, re-derive the actual on-disk file type at `file.Name` (e.g., via `os.Lstat`) before applying `lchmod`/`processZipExtra`, and skip metadata application (or use non-following syscalls) if the current on-disk type doesn't match `file.Mode()` from the header used for extraction. Alternatively, reject archives containing duplicate `Name` entries, and/or replace `os.Chtimes` with a symlink-safe alternative (e.g., only call it when `os.Lstat` confirms the target is a regular file/dir, not a symlink).

### Proof of Concept
```go
func TestExtractZipArchive_DuplicateNameSymlinkRedirectsChtimes(t *testing.T) {
    tmpDir := t.TempDir()
    outsideTarget := filepath.Join(t.TempDir(), "outside.txt")
    require.NoError(t, os.WriteFile(outsideTarget, []byte("victim"), 0o644))
    origInfo, _ := os.Stat(outsideTarget)
    origModTime := origInfo.ModTime()

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)

    // Entry A: regular file at "P"
    fh := &zip.FileHeader{Name: "P", Modified: time.Now()}
    fh.SetMode(0o644)
    w, _ := zw.CreateHeader(fh)
    _, _ = w.Write([]byte("regular content"))

    // Entry B: symlink at same "P", pointing outside the archive root
    lh := &zip.FileHeader{Name: "P"}
    lh.SetMode(os.ModeSymlink | 0o777)
    lw, _ := zw.CreateHeader(lh)
    _, _ = lw.Write([]byte(outsideTarget))

    zw.Close()

    os.Chdir(tmpDir)
    r, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    _ = archives.ExtractZipArchive(r)

    newInfo, _ := os.Lstat(outsideTarget)
    // Assert: outsideTarget's mtime was changed by the second-pass Chtimes call,
    // proving the symlink from entry B was followed using entry A's regular-file header.
    require.NotEqual(t, origModTime, newInfo.ModTime())
}
```
Expected assertion: `outsideTarget`'s modification time changes despite it never being referenced directly in the archive, confirming Chtimes followed the redirected symlink at path `P`.