### Title
Zip-slip path traversal in `ExtractZipArchive` allows extraction outside the assigned root - (File: helpers/archives/zip_extract.go)

### Summary
`ExtractZipArchive` writes every zip entry using `file.Name` verbatim — via `os.Mkdir`, `os.OpenFile`, `os.Symlink`, and `filepath.Dir` for parent creation — without ever canonicalizing the entry against the intended extraction root or rejecting `..`/absolute-path segments. Go's `archive/zip` reader does not sanitize `File.Name` on read, so a crafted archive (cache or artifact) can place entries like `../../../foo` or an absolute path and have `ExtractZipArchive` write/overwrite files anywhere the runner process can reach.

### Finding Description
`extractZipFile` (helpers/archives/zip_extract.go:61-83) does:
```go
err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
```
and then dispatches to `extractZipDirectoryEntry` (`os.Mkdir(file.Name, ...)`), `extractZipSymlinkEntry` (`os.Symlink(data, file.Name)`), or `extractZipFileEntry` (`os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, ...)`). None of these call `filepath.Clean`/`filepath.Abs` and compare the result against a fixed root, nor reject paths starting with `/` or containing `..` segments.

`ExtractZipArchive` (zip_extract.go:85-110) loops over `archive.File` and calls `errorIfGitDirectory` (helpers/archives/path_check_helper.go:21-31), but that check only detects a leading `.git` path component to warn about `.git` overwrite — it does nothing to block `..` traversal or absolute paths, and even that check is advisory only (logged via `tracker.actionable`, extraction still proceeds regardless of the returned error).

The zip-consuming callers (e.g. `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`) rely on the *current working directory* having already been set to the extraction root before calling `archives.ExtractZipArchive(zr)`; the `extractor.dir` field is stored but never used to enforce that entries stay under it. Since `os.MkdirAll`/`os.OpenFile`/`os.Symlink` resolve relative paths against CWD, any entry name containing `../` sequences (or an absolute path) escapes that root entirely.

Attacker path: an unprivileged pipeline author controls the contents of a cache or artifact archive uploaded from their own job (e.g. `cache: paths:` glob captured into a zip, or a build script that fabricates its own zip and pushes it as an artifact). When that archive is later restored (own job cache restore, or a downstream job/pipeline pulling the artifact), `ExtractZipArchive` extracts entries such as `../../../../home/gitlab-runner/.ssh/authorized_keys` or `../../builds/<other-project>/config` without any root check, overwriting files outside the intended cache/build directory.

### Impact Explanation
An attacker who controls archive content consumed via cache or artifact restore can write or overwrite arbitrary files reachable by the runner/build user outside the designated cache/build directory — for example other jobs' checkout state, helper binaries, or files under the runner's home directory — potentially leading to cross-job state tampering, corruption of unrelated job workspaces on shared runner hosts, or execution-path hijacking if a subsequently-executed script/binary is overwritten. This matches the "cross-job state tampering via path-root escape" impact class.

### Likelihood Explanation
This requires only that an attacker controls or influences the bytes of a zip archive that Runner later extracts (their own job's cache/artifact, or shared-runner cache reused across jobs/projects). Since Go's `archive/zip` package does not sanitize `File.Name`, and the runner extraction code performs no root-containment check, crafting a malicious zip entry is trivial and fully attacker-controlled (no special privilege beyond running a normal CI job that produces/uploads an archive). The bug is deterministic and repeatable on every extraction of such an archive.

### Recommendation
In `extractZipFile` (and the analogous tar extractor), resolve each entry against the known extraction root with `filepath.Clean`/`filepath.Join(root, file.Name)`, then verify with `filepath.Rel` or a prefix check that the resolved path still lies under `root` before calling `os.Mkdir`, `os.OpenFile`, or `os.Symlink`; reject (abort extraction, not just warn) any entry with an absolute path or that escapes the root, mirroring how `errorIfGitDirectory` is checked but making it a hard failure rather than a log-only signal.

### Proof of Concept
```go
func TestExtractZipArchive_PathTraversal(t *testing.T) {
    tmpRoot := t.TempDir()
    escapeTarget := filepath.Join(tmpRoot, "..", "escaped.txt")

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    w, _ := zw.Create("../escaped.txt")
    _, _ = w.Write([]byte("pwned"))
    _ = zw.Close()

    zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))

    extractDir := filepath.Join(tmpRoot, "extract")
    _ = os.MkdirAll(extractDir, 0o755)
    cwd, _ := os.Getwd()
    defer os.Chdir(cwd)
    _ = os.Chdir(extractDir)

    err := archives.ExtractZipArchive(zr)
    require.NoError(t, err)

    // Assert file did NOT escape extractDir
    _, statErr := os.Stat(escapeTarget)
    require.True(t, os.IsNotExist(statErr), "zip-slip: file escaped extraction root")
}
```
Expected: with current code, `escapeTarget` exists (test fails, demonstrating the vuln); after adding root-containment validation, extraction must fail/skip the entry and `escapeTarget` must not exist.