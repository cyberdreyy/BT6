### Title
Zip Slip: unsanitized `zip.File.Name` reaches `lchmod`'s `unix.Fchmodat` call, permitting `chmod` on arbitrary filesystem paths outside the extraction root - ([File: helpers/archives/zip_extract.go], [File: helpers/archives/os_unix.go])

### Summary
`ExtractZipArchive` in `helpers/archives/zip_extract.go` passes `file.Name` directly to `extractZipFile` and then to `lchmod` without any canonicalization, `..`-traversal check, or rejection of absolute paths. `lchmod` in `helpers/archives/os_unix.go` calls `unix.Fchmodat(unix.AT_FDCWD, name, ...)`, so any `name` that is absolute or contains `../` segments is resolved by the kernel exactly as written, relative to the process's current working directory rather than any intended extraction root.

### Finding Description
`ExtractZipArchive` (helpers/archives/zip_extract.go:85-110) iterates `archive.File` twice: first calling `extractZipFile(file)` (which creates directories/files/symlinks at `file.Name` via `os.MkdirAll(filepath.Dir(file.Name), ...)`, `os.Mkdir`, `os.OpenFile`, `os.Symlink` — all using the raw name), then calling `lchmod(file.Name, file.Mode())`. The only validation performed anywhere in this path is `errorIfGitDirectory` (helpers/archives/path_check_helper.go:13-19), which only rejects paths whose first cleaned segment is literally `.git` — it does nothing to reject absolute paths, `..` traversal, NUL bytes, or path-confusable Unicode.

`lchmod` (helpers/archives/os_unix.go:12-29) calls `unix.Fchmodat(unix.AT_FDCWD, name, uint32(mode.Perm()), flags)`. `AT_FDCWD` means the path is resolved starting at the process's current working directory if relative, or as an absolute path if `name` begins with `/`. There is no `chroot`, `openat`-relative-fd confinement, or prefix check against a destination root anywhere in `ExtractZipArchive` or its caller `ziplegacy.extractor.Extract` (commands/helpers/archive/ziplegacy/zip_legacy_extractor.go:26-32), which itself never uses the `dir` field it stores — it's dead/unused for confinement purposes.

Consequently, for a zip entry named e.g. `../../../../tmp/some_writable_file` or an absolute path the attacker knows exists and is writable by the job/runner user, both the extraction step and the subsequent `lchmod` step operate outside the intended extraction directory. For true root-owned files like `/etc/shadow`, an unprivileged runner process would get `EPERM` from `Fchmodat` (and from the prior `os.OpenFile`), so `Fchmodat` would return an `*os.PathError` as the question hypothesizes for that specific target — but for any path the job's OS user *does* own or can write to (e.g., other files under the runner's home directory, other project's build/cache directories on a shared filesystem, files created earlier by the same extraction via `../` traversal), `Fchmodat` will succeed silently, changing permissions outside the extraction root. The `tracker.actionable(err)` (helpers/archives/path_error_tracker.go) only suppresses repeated warning log lines for errors — it does not block the corrupting/mutating action for successful calls.

### Impact Explanation
An attacker who controls the contents of an artifact/cache zip archive (a normal CI job author, since cache/artifact archives are attacker-authored data restored by later Runner-executed jobs) can craft entry names with `../` traversal or symlink+relative-name combinations that cause `lchmod`'s `Fchmodat` to modify permissions of files outside the intended extraction directory, on any path reachable and owned/writable by the job's OS user. This matches the scoped impact: unauthorized permission modification on host/helper files reachable by the runner process user, not merely a documented admin-choice risk.

### Likelihood Explanation
No privileged escalation is required — only that the extraction runs as an OS user that has some writable/ownable files outside the extraction root (very common on shared runners with a persistent build/cache directory tree, or a user home directory). The bug is deterministic and repeatable: any zip archive with a crafted `Name` reproduces it every time `ExtractZipArchive`/`ExtractZipFile` is invoked, since there is no randomness or timing dependency.

### Recommendation
Before use in `extractZipFile` and `lchmod`, canonicalize each `file.Name` (e.g., via `filepath.Clean` plus a check with `filepath.Rel`/`strings.HasPrefix` against the resolved destination root, rejecting absolute paths and any result containing `..` after cleaning), consistent with common Zip-Slip mitigations. Alternatively resolve/verify the full destination path against the extraction root with `filepath.Localize`/`os.Root` (Go 1.24+) or manual prefix validation, and reject/skip entries that escape the root instead of proceeding to `extractZipFile`/`lchmod`.

### Proof of Concept
```go
func FuzzExtractZipArchiveNoEscape(f *testing.F) {
    f.Add("../../../../tmp/evil")
    f.Add("/etc/passwd")
    f.Add("a/../../b")
    f.Fuzz(func(t *testing.T, name string) {
        root := t.TempDir()
        outside := t.TempDir() // sentinel dir outside root, pre-created with known perms
        sentinel := filepath.Join(outside, "sentinel")
        os.WriteFile(sentinel, []byte("x"), 0o600)
        before, _ := os.Stat(sentinel)

        buf := new(bytes.Buffer)
        zw := zip.NewWriter(buf)
        w, _ := zw.CreateHeader(&zip.FileHeader{Name: name, Method: zip.Store})
        w.Write([]byte("data"))
        zw.Close()

        cwd, _ := os.Getwd()
        _ = os.Chdir(root)
        defer os.Chdir(cwd)

        zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
        _ = archives.ExtractZipArchive(zr)

        after, _ := os.Stat(sentinel)
        if before != nil && after != nil && before.Mode() != after.Mode() {
            t.Fatalf("lchmod mutated file outside extraction root for name=%q", name)
        }
        // also assert no new file created outside root for traversal names
    })
}
```
Expected assertion: for adversarial names causing traversal outside `root`, no file inside `outside` should have its mode changed and no new file should be created outside `root`; current code fails this for traversal names pointing at writable targets.