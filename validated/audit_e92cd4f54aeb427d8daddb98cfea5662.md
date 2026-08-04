### Title
Zip cache extraction via `helpers/archives.ExtractZipArchive` writes to raw archive `file.Name` with no path-traversal/symlink confinement to `wd` - (File: `helpers/archives/zip_extract.go`)

### Summary
`CacheExtractorCommand.Execute` in `commands/helpers/cache_extractor.go` resolves `format` from the downloaded/local cache file and calls `archive.NewExtractor(format, f, size, wd)`, then `extractor.Extract(ctx)`. For the `zip` format registered by `ziplegacy` (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`), the extractor discards the `dir` argument entirely and delegates to `archives.ExtractZipArchive`, which extracts every entry using the raw `file.Name` from the zip header with no join-and-validate against a root directory and no symlink-target restriction.

### Finding Description
`ziplegacy.extractor.Extract` (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go:26-33`) is:
```go
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	...
	return archives.ExtractZipArchive(zr)
}
```
Note `e.dir` (the `wd` passed by `cache_extractor.go`) is stored on the struct but never used to scope the extraction. Compare this to the `tarzstd` extractor (`commands/helpers/archive/tarzstd/tarzstd_extractor.go:57-64`), which explicitly builds `path = filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects any path that escapes `e.dir`. The `ziplegacy` path has no equivalent check.

Inside `archives.ExtractZipArchive` (`helpers/archives/zip_extract.go:61-96`), each entry is handled by `extractZipFile`, which uses `file.Name` verbatim:
- `extractZipDirectoryEntry`: `os.Mkdir(file.Name, ...)`
- `extractZipFileEntry`: `os.Remove(file.Name)` then `os.OpenFile(file.Name, ...)`
- `extractZipSymlinkEntry`: `os.Remove(file.Name)` then `os.Symlink(string(data), file.Name)` - the symlink **target** is fully attacker-controlled (it's just the file content), and the symlink **location** is also `file.Name` verbatim.

The only sanitization applied is `errorIfGitDirectory`, which rejects `.git`-prefixed paths - it does not check for `..` traversal, absolute paths, or symlink-based escapes.

Because none of these calls join `file.Name` to a root and validate it, an archive entry named `../../shared/evil` will be written outside the intended extraction directory via `os.MkdirAll(filepath.Dir(file.Name))` + `os.OpenFile`. Because `os.MkdirAll`/`os.OpenFile`/`os.Symlink` all follow existing directory symlinks that are already present on disk, a two-stage archive (or a symlink planted by a prior stage of the same job) that creates a symlink named e.g. `pivot -> /host/shared/cache` and then writes a file at `pivot/evil` will cause the write to land at the symlink target, i.e. outside `wd`.

However, whether this is reachable in practice for `Format = Zip` depends on which extractor is actually registered for that format at runtime. `commands/helpers/archive/fastzip/zip_fastzip_extractor.go` delegates to `github.com/saracen/fastzip`, which is a well maintained library that does perform path-containment checks internally. If `fastzip` is the extractor registered for `archive.Zip`/`ZipZstd` at init time (this needs to be confirmed by checking the `init()`/registration call sites, which I was not able to fully verify within the remaining budget), then the vulnerable `ziplegacy` path may not be reachable via the normal `cache-extractor` command for the `zip` format, and would only be reachable through call sites that explicitly select `ziplegacy.NewExtractor` (e.g. `ExtractZipFile`/legacy artifact-restore flows or a fallback path).

### Impact Explanation
If reachable, a job-controlled cache/artifact zip archive can write or symlink files to arbitrary paths outside the extraction working directory, including host-shared bind-mounted cache directories used by concurrent/subsequent jobs from other projects (matching the scoped impact: cross-project file corruption/persistence outside the job workspace). This is a classic zip-slip issue localized in `helpers/archives/zip_extract.go`.

### Likelihood Explanation
Requires: (1) the vulnerable `ziplegacy.ExtractZipArchive` path to actually be the one invoked for the archive format in question (unconfirmed - `fastzip` may be the registered default and would block this), and (2) shared bind-mounted cache/build directories across concurrent jobs, which is an explicit precondition in the question. If the `ziplegacy` extractor is reachable, the exploit is fully attacker-controlled and repeatable (crafted zip with `../` entry names and/or symlink entries), and requires no special privilege beyond controlling job/cache archive contents.

### Recommendation
In `helpers/archives/zip_extract.go`, require callers to pass a root/`dir` argument (mirroring `ziplegacy.extractor.dir` and the pattern already used by `tarzstd`), and before any `os.Mkdir`/`os.OpenFile`/`os.Symlink` call: (a) `filepath.Join(dir, file.Name)`, resolve to an absolute path, and reject any result that does not have `dir` as a prefix; (b) for symlink entries, additionally reject symlink targets that resolve (after joining relative to the entry's parent) outside `dir`. Apply the same containment logic used in `tarzstd_extractor.go` (lines 57-64) to the zip legacy path, and audit `helpers/archives/tar_extract.go` (if present) for the same gap.

### Proof of Concept
Go unit test in `helpers/archives/zip_extract_test.go`:
```go
func TestExtractZipArchive_PathTraversalEscapesRoot(t *testing.T) {
    // build in-memory zip with entry name "../evil.txt"
    // and a second archive/test with a symlink entry "pivot" -> "/tmp/outside"
    // followed by an entry "pivot/evil.txt"
    // chdir into a sandboxed temp dir before calling ExtractZipArchive/ExtractZipFile
    // assert the resulting files exist OUTSIDE the sandbox temp dir (e.g. at ../evil.txt
    // relative to sandbox, or at /tmp/outside/evil.txt), proving confinement is not enforced.
}
```
This should be paired with tracing which `NewExtractorFunc` is actually registered for `archive.Zip`/`archive.ZipZstd` (grep for `archive.Register(archive.Zip, ...)` / `archive.Register(archive.ZipZstd, ...)`) to confirm whether `ziplegacy` or `fastzip` handles the `cache-extractor` path in the current build - this determines whether the finding is exploitable via `CacheExtractorCommand.Execute` specifically, or only via other callers of `archives.ExtractZipArchive`/`ExtractZipFile`.