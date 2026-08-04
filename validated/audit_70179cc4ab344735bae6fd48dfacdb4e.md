### Title
Zip extraction follows attacker-controlled symlinks, allowing writes outside the extraction root - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipFile` in `helpers/archives/zip_extract.go` creates symlinks from zip entries via `extractZipSymlinkEntry` without validating the link target, and later entries are written with plain `os.OpenFile`/`os.MkdirAll` calls that will transparently follow any symlink already created on disk. Because `ExtractZipArchive` processes `archive.File` in file order with no path containment checks, a crafted archive can place a symlink entry that points outside the extraction root followed by a nested file entry that writes through it.

### Finding Description
`extractZipFile` (`helpers/archives/zip_extract.go:61-83`) dispatches based on `file.Mode()&os.ModeType`. For `os.ModeSymlink` entries, `extractZipSymlinkEntry` (`helpers/archives/zip_extract.go:22-39`) reads the entry's file content as the symlink target and calls `os.Symlink(string(data), file.Name)` with **no validation** that the target stays inside the extraction root (no `filepath.Clean`/`filepath.Rel`/prefix check against the root, unlike the `.git` check via `errorIfGitDirectory`, which only checks the entry name, not symlink targets).

For a subsequent regular-file entry, `extractZipFile` first does `os.MkdirAll(filepath.Dir(file.Name), 0o777)` (line 63) and then `extractZipFileEntry` does `os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, ...)` (line 51). Both `MkdirAll` and `OpenFile` in Go follow symlinks for intermediate path components. If `file.Dir` resolves through a symlink created earlier in the same archive (e.g., `link -> /tmp` then `link/pwned`), the write lands at the symlink target, not inside the intended root.

`ExtractZipArchive` (lines 85-110) loops over `archive.File` in the order they appear in the zip and calls `extractZipFile` for every entry without re-validating that the resolved final path is still under the extraction directory. There is no root-containment enforcement anywhere in this file or in `path_check_helper.go` (which only guards against `.git` paths, not symlink escapes). This code path is reachable from `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`'s `Extract`, which is used by GitLab Runner's cache/artifact extraction helpers, both attacker-controllable inputs (a job can shape the contents of an artifact/cache zip it uploads and that later gets downloaded/extracted by Runner, e.g., in a dependent job or by the same job's cache restore).

### Impact Explanation
An unprivileged job that controls the contents of a cache or artifact zip can escape the intended extraction directory and write arbitrary files to any path reachable by the Runner process's filesystem permissions (e.g., outside the build/cache root, such as `/tmp` or other writable paths, and any path the runner process user can write given an absolute/relative-traversal symlink target). This is a build/cache/artifact-poisoning-induced arbitrary file write outside the intended root, matching the scoped impact.

### Likelihood Explanation
The attack requires only that the attacker control the byte contents of a zip archive that Runner extracts via `ExtractZipArchive`/`ExtractZipFile` (cache archives and artifacts are both attacker-influenced, since a job can create arbitrary cache/artifact zip content). No special executor, elevated privileges, or race conditions are needed — ordering entries within a single zip file is fully attacker-controlled, and both the symlink creation and the traversal write happen deterministically in the same single-threaded loop.

### Recommendation
Before creating a symlink (`extractZipSymlinkEntry`), and before writing any file/directory (`extractZipDirectoryEntry`, `extractZipFileEntry`, and the `MkdirAll` in `extractZipFile`), resolve the final target path with `filepath.Clean`/`filepath.EvalSymlinks`-aware logic and verify it stays within the extraction root (reject or skip the entry otherwise). Additionally, track directories created via symlinks during extraction and refuse to descend into them for subsequent entries, mirroring the mitigations used by hardened tar/zip extractors (e.g., Go's `archive/tar` `Insecure` handling or `securejoin`-style path joins with symlink awareness).

### Proof of Concept
```go
func TestExtractZipArchive_SymlinkEscape(t *testing.T) {
    dir := t.TempDir()
    outsideDir := t.TempDir() // simulate "outside" location, e.g. via symlink target

    zipPath := filepath.Join(dir, "evil.zip")
    f, _ := os.Create(zipPath)
    zw := zip.NewWriter(f)

    // Entry 1: symlink "link" -> outsideDir
    hdr := &zip.FileHeader{Name: "link"}
    hdr.SetMode(os.ModeSymlink | 0o777)
    w, _ := zw.CreateHeader(hdr)
    w.Write([]byte(outsideDir))

    // Entry 2: regular file nested under the symlink
    w2, _ := zw.Create("link/pwned")
    w2.Write([]byte("pwned-content"))

    zw.Close()
    f.Close()

    // Change into extraction root and run extraction
    wd, _ := os.Getwd()
    os.Chdir(dir)
    defer os.Chdir(wd)

    r, _ := zip.OpenReader(zipPath)
    defer r.Close()
    err := archives.ExtractZipArchive(&r.Reader)
    require.NoError(t, err)

    // Assert the file was NOT written outside the root, i.e. NOT via the symlink
    _, err = os.Lstat(filepath.Join(outsideDir, "pwned"))
    assert.True(t, os.IsNotExist(err), "expected file to NOT escape extraction root, but it did")
}
```
Expected current behavior: the assertion fails (the file `pwned` is created inside `outsideDir`), demonstrating the escape. A fixed implementation should either reject the symlink entry, refuse to write through it, or contain the resolved path inside `dir`.