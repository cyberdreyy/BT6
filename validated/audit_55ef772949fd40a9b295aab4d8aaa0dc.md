### Title
lchmod follows attacker-planted symlinks on Linux due to path-name reuse between extraction pass and chmod pass - ([File: helpers/archives/zip_extract.go])

### Summary
`ExtractZipArchive` extracts all zip entries in a first pass and then performs a second pass that calls `lchmod(file.Name, file.Mode())` for every entry using the *original* `zip.File` metadata rather than re-checking what actually exists on disk at that path. Because a later duplicate-named symlink entry can overwrite an earlier duplicate-named regular-file entry during the extraction pass, the chmod pass can end up calling `lchmod` for the earlier (non-symlink) entry against a path that is now a symlink, and on Linux this results in `Fchmodat` being invoked without `AT_SYMLINK_NOFOLLOW`, i.e. the call follows the symlink and chmods whatever it points to.

### Finding Description
`extractZipFileEntry` and `extractZipSymlinkEntry` (`helpers/archives/zip_extract.go` lines 22-59) both unconditionally `os.Remove(file.Name)` before creating the new object, so a zip archive containing two entries with the same `Name` — one a regular file, one `os.ModeSymlink` — will result in "last write wins": if the symlink entry is ordered after the file entry, the final on-disk object at that path is a symlink pointing to attacker-controlled data (the symlink target text, read via `io.ReadAll` in `extractZipSymlinkEntry`, line 30-37), which is fully attacker controlled and can point outside the extraction root.

The chmod pass (`ExtractZipArchive`, lines 98-107) then iterates `archive.File` in its *original* order and calls `lchmod(file.Name, file.Mode())` for **both** entries, using each entry's own stale `Mode()` rather than re-deriving type information from the current on-disk state via `os.Lstat`. For the first (regular-file) entry, `file.Mode()` has no `ModeSymlink` bit set.

Looking at the Linux implementation of `lchmod` (`helpers/archives/os_unix.go` lines 12-28):
```go
func lchmod(name string, mode os.FileMode) error {
	var flags int
	if runtime.GOOS == "linux" {
		if mode&os.ModeSymlink != 0 {
			return nil
		}
	} else {
		flags = unix.AT_SYMLINK_NOFOLLOW
	}
	err := unix.Fchmodat(unix.AT_FDCWD, name, uint32(mode.Perm()), flags)
	...
}
```
On Linux, `flags` stays `0` whenever the *entry's declared mode* is not a symlink — this is true for the first (file) entry even though the path now points to a symlink installed by the second entry. `Fchmodat` with `flags=0` follows symlinks. So `lchmod` for the first entry resolves the now-symlinked path and applies `chmod` to whatever external file the attacker-controlled symlink target references.

None of the existing guards stop this: `errorIfGitDirectory` only blocks `.git` paths; `path_check_helper.go` has no general path-traversal or symlink-target validation; the `pathErrorTracker` only suppresses repeated *log* warnings, not exploit prevention.

### Impact Explanation
An attacker who controls a cache or artifact zip consumed via `CacheExtractorCommand` can cause the runner process to `chmod` an arbitrary file the job's OS user has permission to modify, by pointing the attacker-controlled symlink target at that file (e.g., a world-accessible or job-user-owned file outside the intended extraction root) and choosing a permission mode via the first (file) zip entry's mode bits. This is a permission-modification primitive on files outside the job root — matching the scoped impact ("unauthorized permission change on a file outside the job root"), bounded by the OS permissions of the process/user running the job.

### Likelihood Explanation
This requires only that the attacker fully control a zip archive consumed by the cache/artifact extraction path (a normal pipeline author can shape cache/artifact content), craft two zip entries with identical `Name`, ordered file-then-symlink, and set the symlink target to point at a file outside the extraction root that the job user can otherwise chmod. `archive/zip`/Go's zip writer permits duplicate names, and nothing in `ExtractZipArchive` rejects duplicates. This is deterministic (not a true race — it is a same-process ordering bug, not a TOCTOU race window that needs to be won against a concurrent process), so it is reliably reproducible on Linux.

### Recommendation
In the chmod pass, `os.Lstat` the path immediately before calling `lchmod` and use the actual on-disk file type (skip/no-op if it is currently a symlink, regardless of what the stale `zip.File.Mode()` said), or better, track and chmod immediately after each entry is extracted in the same pass rather than in a second pass driven by stale metadata. Additionally, consider rejecting zip archives containing duplicate entry names outright, since "last write wins" duplicate-name semantics are the root enabler here.

### Proof of Concept
```go
func TestExtractZipDuplicateNameFileThenSymlinkChmodFollowsSymlink(t *testing.T) {
	testInWorkDir(t, func(t *testing.T, fileName string) {
		outsideTarget := createTestFile(t, singleByte) // outside extraction root
		require.NoError(t, os.Chmod(outsideTarget, 0o600))

		f, err := os.Create(fileName)
		require.NoError(t, err)
		defer f.Close()

		zw := zip.NewWriter(f)
		// Entry 1: regular file named "dup", chosen mode e.g. 0777
		w1, _ := zw.CreateHeader(&zip.FileHeader{Name: "dup", Method: zip.Store})
		w1.Write([]byte("x"))
		// Entry 2: symlink named "dup" pointing to outsideTarget (last write wins)
		hdr := &zip.FileHeader{Name: "dup", Method: zip.Store}
		hdr.SetMode(os.ModeSymlink | 0o777)
		w2, _ := zw.CreateHeader(hdr)
		w2.Write([]byte(outsideTarget))
		require.NoError(t, zw.Close())
		f.Close()

		err = ExtractZipFile(fileName)
		require.NoError(t, err)

		// Assert: outsideTarget's permissions were NOT modified by extraction
		fi, err := os.Lstat(outsideTarget)
		require.NoError(t, err)
		assert.Equal(t, os.FileMode(0o600), fi.Mode().Perm(),
			"lchmod incorrectly followed attacker-planted symlink and modified an external file's permissions")
	})
}
```
Expected (buggy) result on Linux: `outsideTarget`'s mode changes to `0777` (or whatever mode entry 1 declared), failing the assertion — proving the chmod pass followed the symlink installed by the duplicate-named entry rather than acting on the path's actual current type.