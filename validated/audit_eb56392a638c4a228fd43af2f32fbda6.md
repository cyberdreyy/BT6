### Title
ziplegacy zip extractor omits the chroot/path-traversal check present in tarzstd extractor, enabling zip-slip - ([File: commands/helpers/archive/ziplegacy/zip_legacy_extractor.go])

### Summary
`ziplegacy.extractor.Extract` calls `archives.ExtractZipArchive(zr)` without ever validating or even using the `dir` field passed to `NewExtractor`, unlike `tarzstd.extractor.Extract` which computes an absolute path and enforces a `strings.HasPrefix(path, e.dir+separator)` chroot check before writing each entry. The underlying `extractZipFile` writes each entry to the raw, attacker-controlled `zip.File.Name` with no sanitization, allowing classic "zip-slip" path traversal (`../../...`) to write files outside the intended extraction directory.

### Finding Description
`ziplegacy.extractor.Extract` (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go:26-33`) does:
```go
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	...
	return archives.ExtractZipArchive(zr)
}
```
Note `e.dir` (the intended extraction root, e.g. `wd` in `artifacts_downloader.go`) is never passed to `ExtractZipArchive` and never used anywhere in the extractor.

`archives.ExtractZipArchive` → `extractZipFile` (`helpers/archives/zip_extract.go:41-83`) uses `file.Name` (the zip entry name taken verbatim from the archive) directly:
```go
err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
...
out, err = os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, file.Mode().Perm())
```
The only check performed is `errorIfGitDirectory` (blocks `.git/...` prefixes), not a path-traversal or dir-confinement check.

Contrast this with `tarzstd.extractor.Extract` (`commands/helpers/archive/tarzstd/tarzstd_extractor.go:57-64`):
```go
path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))
...
if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir {
	return fmt.Errorf("%s cannot be extracted outside of chroot (%s)", path, e.dir)
}
```
This explicitly rejects entries whose resolved absolute path escapes `e.dir`.

Reachable path: `commands/helpers/artifacts_downloader.go:Execute` calls `openArchive(file.Name())` (`artifacts_downloader.go:148-172`), which defaults `format = archive.Zip` unless zstd or gzip magic bytes are detected — i.e., any attacker-supplied plain zip (the common case for job artifacts) is treated as `archive.Zip`. It then calls `archive.NewExtractor(format, f, size, wd)` and `extractor.Extract(ctx)`. When the registered extractor for `archive.Zip`/`archive.ZipZstd` is `ziplegacy.NewExtractor` (this is the extractor explicitly registered by `ziplegacy`'s `init()` at `zip_legacy_archiver.go:16-21`, and is exercised directly in tests such as `helpers_archiver_test.go`'s `"fastzip->legacy"`/`"zstd->legacy"` cases and `OnEachZipExtractor`), no chroot enforcement exists at all. The artifact contents are entirely attacker-controlled: a job author can produce artifacts (`artifacts:paths`) containing crafted zip entries with `../../` sequences, which later get downloaded and extracted by the `artifacts-downloader` internal helper (or by `cache_extractor.go`, which uses the same `archive.NewExtractor` pattern).

No existing check stops this: `errorIfGitDirectory` only blocks `.git` prefix names; there is no `filepath.Abs`/`filepath.Clean`/prefix check anywhere in the zip-legacy path, unlike the tar/zstd path.

### Impact Explanation
An attacker who controls a job's artifact contents can cause the runner host to write arbitrary files outside the intended build directory (e.g., overwrite files in the runner's home directory, shell profiles, or other locations reachable by the runner process user), when that archive is later extracted via the legacy zip extractor path (artifact download or cache extraction). This is a build/cache/artifact-root escape — a violation of the "File operations must stay within intended build/cache/artifact roots" invariant — and can lead to file overwrite/persistence on the runner host, impacting subsequent jobs run by the same runner process/user.

### Likelihood Explanation
Precondition: the zip must be processed via the `ziplegacy` extractor rather than `fastzip`. This code path is real and reachable — it's the extractor registered by the `ziplegacy` package's own `init()`, is directly tested (`helpers_archiver_test.go`), and is used for both `archive.Zip` and `archive.ZipZstd` formats, and by `cache_extractor.go` in the same manner as `artifacts_downloader.go`. Which extractor ultimately wins registration for `archive.Zip` in the shipped binary depends on Go package init ordering between `fastzip` and `ziplegacy` (both are imported in `commands/helpers/archiver.go`), which I could not fully verify from available context — this is the one open uncertainty. Regardless of which wins by default, `ziplegacy.NewExtractor`/`Extract` is a genuine, exercised, reachable function with a real missing check, directly comparable and inferior to the `tarzstd` implementation as the question describes. Constructing a malicious zip with traversal entries is trivial and repeatable (standard zip-slip PoC).

### Recommendation
Add the same absolute-path + prefix confinement check used in `tarzstd.extractor.Extract` to the zip extraction path: resolve each `file.Name` via `filepath.Abs(filepath.Join(e.dir, file.Name))`, reject any entry whose resolved path does not have `e.dir+separator` as a prefix (or does not equal `e.dir`), and pass `e.dir` into `archives.ExtractZipArchive`/`extractZipFile` so it is actually used to confine extraction (currently it isn't even threaded through).

### Proof of Concept
Go unit test (`helpers/archives` or `commands/helpers/archive/ziplegacy`):
```go
func TestZipLegacyExtractorPathTraversal(t *testing.T) {
    dir := t.TempDir()
    subdir := filepath.Join(dir, "build")
    require.NoError(t, os.MkdirAll(subdir, 0777))

    // Build malicious zip with traversal entry
    buf := &bytes.Buffer{}
    zw := zip.NewWriter(buf)
    w, _ := zw.Create("../../evil.txt")
    w.Write([]byte("pwned"))
    zw.Close()

    r := bytes.NewReader(buf.Bytes())
    extractor, err := ziplegacy.NewExtractor(r, int64(r.Len()), subdir)
    require.NoError(t, err)

    // chdir into subdir to emulate real extraction cwd context
    origWd, _ := os.Getwd()
    os.Chdir(subdir)
    defer os.Chdir(origWd)

    err = extractor.Extract(context.Background())
    // Compare with tarzstd behavior on same relative traversal path
    // Expect: err should be non-nil (rejected), same as tarzstd would produce
    assert.Error(t, err, "ziplegacy extractor should reject traversal entries like tarzstd does")

    _, statErr := os.Stat(filepath.Join(dir, "evil.txt"))
    assert.True(t, os.IsNotExist(statErr), "file must not be written outside extraction root")
}
```
Expected current (buggy) result: `err` is `nil` and `evil.txt` is created at `dir/evil.txt` (one level above `subdir`), proving the escape; a fixed implementation should return an error and never create the file outside `dir`.