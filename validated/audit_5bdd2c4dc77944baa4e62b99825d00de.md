### Title
Path traversal (Zip Slip) in legacy zip extractor lacks containment check present in tar+zstd extractor - ([File: helpers/archives/zip_extract.go])

### Summary
`tarzstd.extractor.Extract` explicitly joins entry names to the target directory and rejects any path escaping it (`strings.HasPrefix(path, e.dir+string(filepath.Separator))`), but `archives.ExtractZipArchive` (used by `ziplegacy.extractor.Extract`) writes files/symlinks using the raw `file.Name` from the zip header with no join-and-verify step. A crafted zip artifact with an entry name such as `../../x` will be written outside the extraction directory when processed by the legacy zip path, while the same relative-traversal payload is rejected by the tar+zstd path — a concrete divergence in containment enforcement across archive formats.

### Finding Description
- In `commands/helpers/archive/tarzstd/tarzstd_extractor.go` (lines 57-64), every entry's target path is computed as `filepath.Abs(filepath.Join(e.dir, hdr.Name))` and then checked against `e.dir` before any filesystem write occurs.
- In `helpers/archives/zip_extract.go`, `extractZipFile` (lines 61-83), `extractZipFileEntry` (41-59), and `extractZipSymlinkEntry` (22-39) all operate directly on `file.Name` — the attacker-controlled zip entry name straight from `archive/zip.File` — with **no `filepath.Join(dir, ...)` and no `HasPrefix`/chroot check at all**. `os.MkdirAll(filepath.Dir(file.Name), ...)`, `os.OpenFile(file.Name, ...)`, and `os.Symlink(string(data), file.Name)` are called on the unsanitized name.
- `ExtractZipArchive` (lines 85-110) only screens for `.git` directory entries via `errorIfGitDirectory` (warning only, not blocking) — it performs no path-containment validation whatsoever.
- This `ExtractZipArchive` function is invoked by `ziplegacy.extractor.Extract` (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`, line 32), which is registered under `archive.Zip` format and reachable from `commands/helpers/artifacts_downloader.go` (`ArtifactsDownloaderCommand.Execute`, lines 125-140) whenever the downloaded artifact's magic bytes don't match zstd/gzip (the default fallback is `archive.Zip`, see `openArchive`, lines 148-172).
- The current default zip extractor registered for the `Zip` format in GitLab Runner is `fastzip` (`commands/helpers/archive/fastzip/zip_fastzip_extractor.go`), which delegates to the `saracen/fastzip` library — a library that internally sanitizes/validates extraction paths. The vulnerable `ziplegacy` path is reachable only when the legacy zip extractor is selected instead of fastzip (historically gated by a feature flag / fallback path in the Runner's extractor registration). I was not able to confirm from the available index whether that selection is influenced by any job/pipeline-controlled input, or is purely an operator/build-time configuration choice.

### Impact Explanation
If the legacy zip extractor path is exercised, an attacker who can produce (or influence the raw bytes of) an artifact `.zip` file with entries containing `../` sequences can write, overwrite, or symlink files at arbitrary paths reachable by the process user outside the intended artifact/build directory — a classic Zip Slip / path traversal impacting file-operation containment invariants for artifact extraction, scoped exactly to "format-dependent path traversal in artifact extraction" as asked.

### Likelihood Explanation
The root-cause code defect (missing containment check in `ExtractZipArchive`) is unconditionally present and trivially demonstrable via a differential unit test — this part of the claim is proven. However, real-world exploitability depends on the legacy zip extractor actually being the one invoked at runtime for a given job's artifact download, which (based on the code reachable in this index) is not the default (`fastzip` is registered for `Zip` format and used in the artifact-download path shown). I could not fully verify, within the available context, any attacker-controlled mechanism (job config, feature flag exposed to pipeline authors, magic-byte spoofing, etc.) that forces the runner to use `ziplegacy` instead of `fastzip` for a normal artifact download. This limits confidence in end-to-end exploitability by an unprivileged pipeline author, even though the code-level inconsistency between the two extractors is real and verifiable.

### Recommendation
Add the same containment check used in `tarzstd.extractor.Extract` to `archives.ExtractZipArchive`/`extractZipFile`: compute `path := filepath.Join(dir, file.Name)` (the function needs the target `dir` threaded through, since it currently doesn't receive it) and reject entries where `!strings.HasPrefix(filepath.Clean(path), dir+string(filepath.Separator)) && path != dir`, applying this to both file and symlink extraction before any `os.Mkdir`/`os.OpenFile`/`os.Symlink` call. This makes containment enforcement consistent between all archive-format extractors registered under `commands/helpers/archive`.

### Proof of Concept
Differential Go unit test (place under `helpers/archives` and `commands/helpers/archive/tarzstd`, or a shared test comparing both):
```go
func TestZipVsTarZstdPathContainment(t *testing.T) {
    dir := t.TempDir()

    // Build a zip in memory with a traversal entry "../../evil"
    var zipBuf bytes.Buffer
    zw := zip.NewWriter(&zipBuf)
    fw, _ := zw.Create("../../evil")
    fw.Write([]byte("pwned"))
    zw.Close()
    zr, _ := zip.NewReader(bytes.NewReader(zipBuf.Bytes()), int64(zipBuf.Len()))

    os.Chdir(dir) // ExtractZipArchive uses file.Name relative to cwd
    zipErr := archives.ExtractZipArchive(zr)

    // Build tar+zstd with the same traversal entry
    tarZstdErr := extractTarZstdWithEntry(t, dir, "../../evil") // helper wraps tarzstd.NewExtractor(...).Extract

    // Assert: currently zipErr == nil (traversal succeeds) while tarZstdErr != nil (rejected) -> divergence
    if (zipErr == nil) != (tarZstdErr == nil) {
        t.Fatalf("containment divergence: zip err=%v, tarzstd err=%v", zipErr, tarZstdErr)
    }
    // After fix, both should return non-nil path-containment errors.
}
```
Expected current result: `tarZstdErr` is non-nil ("cannot be extracted outside of chroot"), `zipErr` is `nil` and a file is created at `../../evil` relative to `dir` — proving the divergence. After the recommended fix, both should return equivalent containment errors.