### Title
Legacy zip extractor performs no root-containment check, allowing cache archive entries to write outside the assigned extraction directory (Zip-Slip) - (File: `helpers/archives/zip_extract.go`, `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`)

### Summary
The function named in the question, `getCache` in `commands/helpers/cache_extractor.go`, is purely an HTTP fetch helper (issues a GET, checks for `404`, hands the body to the retry/caller logic) and contains no path or extraction logic at all. [1](#0-0) 
The actual extraction of a downloaded cache archive happens later in `CacheExtractorCommand.Execute`, via `archive.NewExtractor(format, f, size, wd)` followed by `extractor.Extract(ctx)`. [2](#0-1) 
Comparing the registered extractor implementations shows that the `tarzstd` extractor enforces containment of extracted paths inside the target directory, but the legacy zip extractor path (`ziplegacy` → `helpers/archives.ExtractZipArchive`) performs no such check and writes files using the raw, attacker-controlled `zip.File.Name` with no join/verification against the extraction root at all.

### Finding Description
`archive.NewExtractor` dispatches to one of several registered per-format implementations based on the archive `format` argument, all sharing the same `Extractor` interface contract of "extract to the directory provided." [3](#0-2) [4](#0-3) 

The `tarzstd` extractor correctly enforces this contract: for every archive entry it joins the entry name against the extraction directory, resolves it to an absolute path, and rejects any path that is not a descendant of `e.dir`: [5](#0-4) 

By contrast, the legacy zip extractor's `Extract` method ignores its own `e.dir` field entirely and simply calls `archives.ExtractZipArchive(zr)`, which has no knowledge of any extraction root: [6](#0-5) 

Inside `ExtractZipArchive`, each `zip.File` is extracted using `file.Name` directly — there is no `filepath.Join` against any base directory, no `filepath.Abs`/prefix check, and no rejection of `..` segments or absolute paths. The only validation performed is a `.git`-directory check, which is unrelated to path containment: [7](#0-6) [8](#0-7) [9](#0-8) 

Go's standard `archive/zip` package does not sanitize `File.Name` for traversal sequences (`../../`) or absolute paths — that responsibility is left to the caller. Because `extractZipFileEntry`/`extractZipSymlinkEntry` use `file.Name` verbatim as the destination path (relative to the process's current working directory, not the intended cache/build root), a cache archive whose entries contain names like `../../../etc/cron.d/x` or `../../some/trusted/file` will write outside the intended `dir` passed into `NewExtractor`, and can overwrite files elsewhere on the filesystem reachable from the job's working directory tree.

An unprivileged pipeline author fully controls the contents of a cache archive: caches are populated by the user's own job (`cache:` config) and later restored in a subsequent job via presigned URL / GoCloud download in `cache_extractor.go`, with the archive bytes flowing unmodified into `extractor.Extract()`. Cache keys/fallback keys, ETags, and timestamps only affect *which* archive blob is selected (`selectPresignedURL`, `checkIfUpToDate`, etc.) — they do not add any additional sanitization of the archive's internal entry paths before extraction.

### Impact Explanation
If the runner is configured/built to use the legacy zip extractor path (`ziplegacy`) rather than `fastzip` for zip-format caches, an attacker who can influence cache contents (any pipeline author with cache write access) can craft a cache archive that, upon restore in a later stage/job, writes or overwrites files outside the assigned cache/build root — directly matching the scoped impact of "path-root escape and later stronger-context overwrite." This could overwrite trusted files consumed by subsequent job steps (e.g., scripts, config files) executed with the job's privileges.

### Likelihood Explanation
Preconditions: the job/runner must select the legacy zip extractor for cache extraction (as opposed to `fastzip`, which delegates path handling to the third-party `saracen/fastzip` library). I was not able to fully confirm, within the available index, the exact conditions (build tags, environment, or OS) under which `ziplegacy` versus `fastzip` is selected at registration time — this would need to be verified in a full checkout (e.g. `grep` for `ziplegacy.NewExtractor`/`fastzip.NewExtractor` registration call sites, which were not returned by the available search). If `ziplegacy` is reachable for a normal user's zip-format cache, the exploit is fully repeatable and requires no special privileges — only control over cache archive bytes, which any pipeline author has by design.

### Recommendation
Add the same containment check used in `tarzstd_extractor.go` (join against `dir`, resolve absolute path, verify the result is a descendant of `dir`) to `helpers/archives/zip_extract.go`'s `extractZipFile`/`ExtractZipArchive`, or update `ziplegacy.extractor.Extract` to pass `e.dir` through and have `ExtractZipArchive` reject or rewrite unsafe entry names (traversal sequences, absolute paths, symlink targets escaping the root) before writing. Symlink targets (`extractZipSymlinkEntry`) should also be validated to stay within the extraction root, not just the symlink path itself.

### Proof of Concept
Go unit test plan for `helpers/archives/zip_extract_test.go`:
1. Build an in-memory zip archive with `archive/zip.Writer` containing a single entry named `../pwned.txt` (or `/tmp/pwned.txt` on Unix) with attacker content.
2. Create a fresh empty temp directory `root`, `chdir` into a subdirectory of it (simulating an executor cache directory), and call `ExtractZipFile`/`ExtractZipArchive` on the crafted archive (as `ziplegacy.extractor.Extract` would).
3. Assert that no file was created outside `root` — currently this assertion fails because `../pwned.txt` is written one directory above the intended extraction root, since `extractZipFile` uses `file.Name` unmodified.
4. Add an equivalent regression test mirroring `tarzstd_extractor_test.go`'s containment test (`"cannot be extracted outside of chroot"`) for the zip path to lock in the fix.

### Citations

**File:** commands/helpers/cache_extractor.go (L192-204)
```go
func (c *CacheExtractorCommand) getCache(rawURL string) (*http.Response, error) {
	resp, err := c.getClient().Get(rawURL)
	if err != nil {
		return nil, retryableErr{err: err}
	}

	if resp.StatusCode == http.StatusNotFound {
		_ = resp.Body.Close()
		return nil, os.ErrNotExist
	}

	return resp, retryOnServerError(resp)
}
```

**File:** commands/helpers/cache_extractor.go (L646-663)
```go
	f, size, format, err := openArchive(c.File)
	if os.IsNotExist(err) {
		warningln("Cache file does not exist")
	}
	if err != nil {
		logrus.Fatalln(err)
	}
	defer f.Close()

	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
	}
```

**File:** commands/helpers/archive/archive.go (L52-63)
```go
// Extractor is an interface for the Extract method.
type Extractor interface {
	Extract(ctx context.Context) error
}

// NewArchiverFunc is a function that can be registered (with Register()) and
// used to instantiate a new archiver (with NewArchiver()).
type NewArchiverFunc func(w io.Writer, dir string, level CompressionLevel) (Archiver, error)

// NewExtractorFunc is a function that can be registered (with Register()) and
// used to instantiate a new extractor (with NewExtractor()).
type NewExtractorFunc func(r io.ReaderAt, size int64, dir string) (Extractor, error)
```

**File:** commands/helpers/archive/archive.go (L99-109)
```go
// NewExtractor returns a new Extractor of the specified format.
//
// The extractor will extract files to the directory provided.
func NewExtractor(format Format, r io.ReaderAt, size int64, dir string) (Extractor, error) {
	fn := extractors[format]
	if fn == nil {
		return nil, fmt.Errorf("%q format: %w", format, ErrUnsupportedArchiveFormat)
	}

	return fn(r, size, dir)
}
```

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L57-64)
```go
		var path string
		path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))
		if err != nil {
			return err
		}
		if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir {
			return fmt.Errorf("%s cannot be extracted outside of chroot (%s)", path, e.dir)
		}
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L24-33)
```go
// Extract extracts files from the reader to the directory passed to
// NewZipExtractor.
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
}
```

**File:** helpers/archives/zip_extract.go (L41-59)
```go
func extractZipFileEntry(file *zip.File) (err error) {
	var out *os.File
	in, err := file.Open()
	if err != nil {
		return err
	}
	defer func() { _ = in.Close() }()

	// Remove file before creating a new one, otherwise we can error that file does exist
	_ = os.Remove(file.Name)
	out, err = os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, file.Mode().Perm())
	if err != nil {
		return err
	}
	defer func() { _ = out.Close() }()
	_, err = io.Copy(out, in)

	return
}
```

**File:** helpers/archives/zip_extract.go (L85-96)
```go
func ExtractZipArchive(archive *zip.Reader) error {
	tracker := newPathErrorTracker()

	for _, file := range archive.File {
		if err := errorIfGitDirectory(file.Name); tracker.actionable(err) {
			printGitArchiveWarning("extract")
		}

		if err := extractZipFile(file); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}
	}
```

**File:** helpers/archives/path_check_helper.go (L13-31)
```go
func isPathAGitDirectory(path string) bool {
	parts := strings.Split(filepath.Clean(path), string(filepath.Separator))
	if len(parts) > 0 && parts[0] == ".git" {
		return true
	}
	return false
}

func errorIfGitDirectory(path string) *os.PathError {
	if !isPathAGitDirectory(path) {
		return nil
	}

	return &os.PathError{
		Op:   ".git inside of archive",
		Path: path,
		Err:  errors.New("trying to archive or extract .git path"),
	}
}
```
