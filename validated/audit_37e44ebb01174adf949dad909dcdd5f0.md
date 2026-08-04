### Title
Zip extraction path traversal via unconfined `file.Name` (missing chroot check present in tarzstd) - ([File: commands/helpers/archive/ziplegacy/zip_legacy_extractor.go], [File: helpers/archives/zip_extract.go])

### Summary
The production Zip extraction path (`ziplegacy.extractor.Extract` → `archives.ExtractZipArchive`) performs zero directory-confinement validation on archive entry names, while the `tarzstd` extractor's `Extract` enforces `strings.HasPrefix(path, e.dir+separator)` before writing any file. Since both extractors are reachable through the same `archive.NewExtractor(format, r, size, wd)` dispatch used by cache/artifact extraction, an attacker who controls a `zip`-formatted cache/artifact archive can write files anywhere the runner process has permission to, while the identical payload in `tarzstd` format is rejected.

### Finding Description
`CacheExtractorCommand.Execute` and `ArtifactsDownloaderCommand.Execute` both call `archive.NewExtractor(format, f, size, wd)` with `wd` (the job's working directory) as the intended extraction root [1](#0-0) [2](#0-1) .

For `tarzstd`, `e.dir` is actually used: every entry path is resolved to an absolute path and checked with `strings.HasPrefix(path, e.dir+string(filepath.Separator))`, rejecting any `../`-based escape with an explicit error [3](#0-2) .

For `zip` (registered by `ziplegacy.NewExtractor`, which wins the `archive.Zip` registration via `archive.Register(archive.Zip, NewArchiver, NewExtractor)`), the `dir` field is stored on the `extractor` struct but is **never referenced** in `Extract()` — it just opens the zip reader and calls `archives.ExtractZipArchive(zr)` [4](#0-3) [5](#0-4) .

`ExtractZipArchive` iterates `archive.File` and only calls `errorIfGitDirectory(file.Name)` (which merely detects a leading `.git` path component and logs a warning, still proceeding with extraction) before calling `extractZipFile(file)` [6](#0-5) . `extractZipFile` uses `file.Name` directly with `os.MkdirAll(filepath.Dir(file.Name), ...)` and `os.OpenFile(file.Name, ...)` — a relative path taken verbatim from the archive header, with no `..`-rejection, no `filepath.Abs`/`HasPrefix` chroot check at all [7](#0-6) . `errorIfGitDirectory`/`isPathAGitDirectory` only detect a literal `.git` first path segment — they do not detect or block `../` traversal sequences [8](#0-7) .

Since extraction happens with the process's current working directory equal to `wd` (job build dir), a zip entry such as `../../../etc/cron.d/evil` or `../victim-project/.git/hooks/pre-commit` resolves relative to cwd via `os.OpenFile`/`os.MkdirAll`, escaping the intended directory. The identical traversal payload re-encoded as a `tarzstd` archive is rejected by the `strings.HasPrefix` check before any file is created.

### Impact Explanation
An unprivileged pipeline author who controls a cache/artifact archive (cache key/format is job-config-controlled; format detection between zip/tar.zst is by magic bytes in `openArchive`) can cause the runner to write arbitrary files outside the build directory during cache/artifact extraction — e.g., overwriting files in a sibling project's checkout under a shared builds root, or writing to any path the runner process user can reach. This directly matches the "path traversal protection must be uniform across all supported archive formats" invariant: the same attacker-controlled input passes for `zip` and fails for `tarzstd`, producing a format-selection bypass of the confinement control that exists elsewhere in the codebase (also present in `fastzip`'s underlying library and `tarzstd`'s own archiver-side check).

### Likelihood Explanation
Fully attacker-reachable with no special privileges: cache/artifact archives are produced from job-controlled content (or can be supplied via a `.gitlab-ci.yml`-controlled cache restore/artifact download using a crafted archive if the attacker can influence what gets cached/uploaded, or via a compromised previous stage's artifact). The `zip` format is the default (`openArchive` defaults to `archive.Zip` unless zstd/gzip magic bytes are detected) [9](#0-8) , making this trivially reachable and repeatable — no race conditions or timing dependencies.

### Recommendation
Add the same directory-confinement check used by `tarzstd`/`fastzip` into `ExtractZipArchive`/`extractZipFile`: resolve each `file.Name` against the target extraction directory with `filepath.Abs`/`filepath.Join` and reject entries whose resolved path is not prefixed by `dir+separator` (and not equal to `dir`). This requires threading the extraction `dir` from `ziplegacy.extractor.Extract` into `archives.ExtractZipArchive` (currently dropped), and applying an equivalent check inside `extractZipFile`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry` before any `os.Mkdir`/`os.OpenFile`/`os.Symlink` call.

### Proof of Concept
```go
// helpers/archives/zip_extract_traversal_test.go
func TestExtractZipArchive_PathTraversal(t *testing.T) {
    tmpDir := t.TempDir()
    victimDir := t.TempDir() // simulate sibling project outside intended root
    require.NoError(t, os.Chdir(tmpDir))

    // craft a zip with an entry that escapes tmpDir into victimDir
    escapePath := filepath.Join("..", filepath.Base(victimDir), "pwned.txt")
    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    w, _ := zw.Create(escapePath)
    _, _ = w.Write([]byte("evil"))
    zw.Close()

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)

    err = ExtractZipArchive(zr) // current behavior: err == nil, file written outside tmpDir
    // EXPECTED (fixed): err != nil, mentioning "cannot be extracted outside"
    assert.Error(t, err)
    assert.NoFileExists(t, filepath.Join(victimDir, "pwned.txt"))
}
```
Companion differential test: build the same relative-path entry into a `tarzstd` archive via `tarzstd.NewExtractor(...).Extract(ctx)` and assert it errors with `"cannot be extracted outside of chroot"`, while asserting the zip extractor currently succeeds — proving the behavioral divergence described.

### Citations

**File:** commands/helpers/cache_extractor.go (L655-660)
```go
	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	err = extractor.Extract(context.Background())
```

**File:** commands/helpers/artifacts_downloader.go (L125-140)
```go
	f, size, format, err := openArchive(file.Name())
	if err != nil {
		logrus.Fatalln(err)
	}
	defer f.Close()

	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	// Extract artifacts file
	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
	}
```

**File:** commands/helpers/artifacts_downloader.go (L148-164)
```go
func openArchive(filename string) (*os.File, int64, archive.Format, error) {
	format := archive.Zip

	f, err := os.Open(filename)
	if err != nil {
		return nil, 0, format, err
	}

	var magic [4]byte
	_, _ = f.Read(magic[:])
	_, _ = f.Seek(0, io.SeekStart)
	switch {
	case bytes.HasPrefix(magic[:], zstMagic):
		format = archive.TarZstd
	case bytes.HasPrefix(magic[:], gzipMagic):
		format = archive.Gzip
	}
```

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L56-64)
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L12-33)
```go
// extractor is a zip stream extractor.
type extractor struct {
	r    io.ReaderAt
	size int64
	dir  string
}

// NewExtractor returns a new Zip Extractor.
func NewExtractor(r io.ReaderAt, size int64, dir string) (archive.Extractor, error) {
	return &extractor{r: r, size: size, dir: dir}, nil
}

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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_archiver.go (L16-21)
```go
func init() {
	zip.RegisterDecompressor(zstd.ZipMethodWinZip, fastzip.ZstdDecompressor())

	archive.Register(archive.Zip, NewArchiver, NewExtractor)
	archive.Register(archive.ZipZstd, nil, NewExtractor)
}
```

**File:** helpers/archives/zip_extract.go (L41-83)
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

func extractZipFile(file *zip.File) (err error) {
	// Create all parents to extract the file
	err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
	if err != nil {
		return err
	}

	switch file.Mode() & os.ModeType {
	case os.ModeDir:
		err = extractZipDirectoryEntry(file)

	case os.ModeSymlink:
		err = extractZipSymlinkEntry(file)

	case os.ModeNamedPipe, os.ModeSocket, os.ModeDevice:
		// Ignore files of these types
		logrus.Warningf("File ignored: %q", file.Name)

	default:
		err = extractZipFileEntry(file)
	}
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
