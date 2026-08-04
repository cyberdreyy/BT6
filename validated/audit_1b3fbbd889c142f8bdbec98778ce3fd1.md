### Title
Zip extraction (`ExtractZipArchive`/`processZipTimestampField`) has no sandbox/path-containment check, allowing symlinked-directory entries to redirect `os.Chtimes` (and file writes) outside the extraction root - (`helpers/archives/zip_extract.go`, `helpers/archives/zip_extra.go`)

### Summary
`helpers/archives/zip_extract.go` extracts zip entries and then runs `processZipExtra`/`processZipTimestampField` using the raw `file.Name` from the archive, with no check that the resolved path stays inside the extraction directory. Because `extractZipSymlinkEntry` lets an attacker create a symlink at any entry name pointing anywhere on disk, and a later entry can use that name as a path component, both the regular file write (`extractZipFileEntry`) and the subsequent `os.Chtimes(file.Name, ...)` in `processZipTimestampField` will follow the symlink and act on the external target.

### Finding Description
`extractZipFile` (`helpers/archives/zip_extract.go:61-83`) switches on `file.Mode()&os.ModeType` and, for `os.ModeSymlink`, calls `extractZipSymlinkEntry` (`zip_extract.go:22-39`), which does `os.Symlink(string(data), file.Name)` with the link target coming directly from the zip entry's file content — fully attacker-controlled, including absolute paths or `../` sequences.

Unlike the tar/zstd extractor (`commands/helpers/archive/tarzstd/tarzstd_extractor.go:57-64`), which explicitly resolves each path with `filepath.Abs`/`filepath.Join` and rejects it if it escapes `e.dir` (`"cannot be extracted outside of chroot"`), `ExtractZipArchive` (`zip_extract.go:85-110`) performs **no equivalent containment check**. It only rejects `.git` paths (`errorIfGitDirectory`) — nothing else validates that `file.Name` (or a directory component of it) stays inside the intended extraction root.

Exploit flow:
1. Archive entry `A` (symlink) with name `linkdir`, content `../../../../tmp` (or any absolute/relative path leaving the sandbox).
2. Archive entry `B` (regular file) with name `linkdir/target`, carrying a `ZipTimestampFieldType` extra field.
3. During extraction, `extractZipFile` creates the symlink `linkdir -> ../../../../tmp`, then for entry `B` calls `os.MkdirAll(filepath.Dir("linkdir/target"))` (a no-op, since `linkdir` already resolves to an existing directory through the symlink) and `extractZipFileEntry`, which does `os.OpenFile("linkdir/target", O_CREATE|O_TRUNC, ...)` — this write follows the symlinked directory component and lands outside the sandbox.
4. In the second pass, `processZipExtra(&file.FileHeader)` → `processZipTimestampField` (`zip_extra.go:50-68`) calls `os.Chtimes(file.Name, acTime, modTime)` with the same `file.Name = "linkdir/target"`. Since `os.Chtimes` resolves symlinked path components, this mutates the mtime of the externally-resolved file, not something under the intended extraction directory.

No existing check (allowed-image checks, overwrite guards, path validation, masking) stops this in `zip_extract.go`. The `.git` check is unrelated, and there is no analogue of the tar extractor's chroot check for zip.

### Impact Explanation
This is a genuine "Zip Slip"-class bug in the pure-Go/legacy zip extractor: it allows arbitrary file **write** to a location outside the intended extraction root (`file.Name` combined with a symlinked directory), and, as a direct consequence, arbitrary `os.Chtimes` mtime manipulation on the resolved target, exactly as the question describes. The write capability is actually the more severe consequence (arbitrary file content control), with mtime tampering (potentially defeating cache-freshness/staleness checks that key off mtimes) as a secondary effect. The blast radius is bounded by filesystem permissions of the process performing extraction (typically the CI job's own user/container filesystem); it does not by itself grant cross-tenant/cross-project access unless the runner host or container filesystem is shared with other projects' data (shell executor with shared paths), which is a pre-existing isolation concern but not required for the Chtimes/write primitive itself to fire.

### Likelihood Explanation
This path is reachable purely with attacker-controlled input: a pipeline author can construct a malicious zip (e.g., an artifact or cache archive) with a symlink entry followed by a same-prefixed regular-file entry carrying a timestamp extra field, using only `archive/zip` from Go or any zip tool that supports raw header/extra manipulation. This is reachable whenever the legacy/pure-Go zip extractor path (`ziplegacy` extractor, which calls `archives.ExtractZipArchive`) is used to extract runner-consumed artifacts/caches — this is a real, deterministic, repeatable Go code path with no gating conditions beyond "attacker controls the archive content," which is standard for CI job artifacts/caches. Note: the default `fastzip` extractor (`commands/helpers/archive/fastzip`) delegates to the external `saracen/fastzip` library, which may have its own protections; this finding is specific to the code paths that use `archives.ExtractZipArchive` (the `ziplegacy` extractor and any direct callers of `ExtractZipFile`/`ExtractZipArchive`).

### Recommendation
Add the same containment check used in the tar/zstd extractor before any operation touches disk in `helpers/archives/zip_extract.go`: resolve each `file.Name` with `filepath.Abs(filepath.Join(dir, file.Name))` and reject entries whose resolved path (or any parent path, including symlinked directory components already extracted) escapes the extraction root. Additionally, for symlink entries, validate that the link target does not point outside the destination directory before calling `os.Symlink`, and when applying `os.Chtimes`/`lchmod` post-extraction, re-verify (e.g., via `filepath.EvalSymlinks` compared against the sandbox root, or preferring `os.Lchtimes`-equivalent semantics where available) that the final resolved path is still inside the sandbox before mutating it.

### Proof of Concept
Go test idea (add to `helpers/archives/zip_extract_test.go`):
```go
func TestExtractZipArchive_SymlinkDirectoryEscape(t *testing.T) {
    dir := t.TempDir()
    outside := t.TempDir()
    outsideFile := filepath.Join(outside, "victim")
    require.NoError(t, os.WriteFile(outsideFile, []byte("orig"), 0o600))

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)

    // symlink "linkdir" -> outside dir
    linkHdr := &zip.FileHeader{Name: "linkdir"}
    linkHdr.SetMode(os.ModeSymlink | 0o777)
    lw, _ := zw.CreateHeader(linkHdr)
    _, _ = lw.Write([]byte(outside))

    // regular file "linkdir/victim" with timestamp extra
    fileHdr := &zip.FileHeader{Name: "linkdir/victim", Method: zip.Deflate}
    fileHdr.SetMode(0o644)
    // attach ZipTimestampFieldType extra with a fixed old modTime
    fw, _ := zw.CreateHeader(fileHdr)
    _, _ = fw.Write([]byte("pwned"))
    zw.Close()

    prevWD, _ := os.Getwd()
    os.Chdir(dir)
    defer os.Chdir(prevWD)

    zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    err := ExtractZipArchive(zr)
    require.NoError(t, err)

    content, _ := os.ReadFile(outsideFile)
    assert.NotEqual(t, "orig", string(content), "file outside sandbox was overwritten via symlinked directory")

    info, _ := os.Stat(outsideFile)
    assert.NotEqual(t, time.Now().Year(), info.ModTime().Year(), "mtime of external file was mutated via Chtimes")
}
```
Expected (current, vulnerable) result: the assertions fail to hold as "safe" — i.e., `outsideFile` content changes to `"pwned"` and its mtime is altered, proving both arbitrary write and the `os.Chtimes`-follows-symlink mtime tampering described in the question.