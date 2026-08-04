### Title
Zip-slip path traversal in legacy zip extractor (`helpers/archives/zip_extract.go`, used by `ArtifactsDownloaderCommand`)

### Summary
The legacy zip extractor used by `archive.NewExtractor(archive.Zip, ...)` when `FF_USE_FASTZIP` is not enabled writes files using the raw `zip.File.Name` from the archive with no path-sanitization, unlike the `tarzstd` extractor which explicitly enforces a chroot-style prefix check. This allows a job-controlled artifact zip with `../`-style entry names to write files outside the intended working directory (`wd`) passed into `ArtifactsDownloaderCommand.Execute`.

### Finding Description
`ArtifactsDownloaderCommand.Execute` computes `wd := os.Getwd()` and calls `archive.NewExtractor(format, f, size, wd)` followed by `extractor.Extract(ctx)` [1](#0-0) . For the `Zip` format, unless the `FF_USE_FASTZIP` feature flag is enabled, the registered extractor is `ziplegacy.NewExtractor` [2](#0-1) , whose `Extract()` method **discards the `dir` (chroot) parameter entirely** and simply calls `archives.ExtractZipArchive(zr)`: [3](#0-2) 

`ExtractZipArchive` iterates `archive.File` and calls `extractZipFile(file)` for each entry, which uses `file.Name` (the raw, attacker-supplied zip header name) directly: `os.MkdirAll(filepath.Dir(file.Name), ...)`, then `os.Remove(file.Name)`/`os.OpenFile(file.Name, ...)` for regular files, or `os.Symlink(string(data), file.Name)` for symlink entries — with no traversal check, no absolute-path rejection, and no confinement to the working directory: [4](#0-3) [5](#0-4) 

Contrast this with `tarzstd.extractor.Extract`, which explicitly computes `path := filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects any entry where `!strings.HasPrefix(path, e.dir+separator)`: [6](#0-5)  — that protection is entirely absent from the legacy zip path.

An unprivileged pipeline author fully controls the contents of their own job's artifacts (e.g. via `artifacts:paths` uploading a hand-crafted zip file, or exploiting a MITM/misconfigured artifact source). By crafting an entry named `../../../etc/cron.d/x` (or an absolute path, since `file.Name` is used unmodified), a downstream job/stage that downloads that artifact via `artifacts-downloader` will extract the file relative to the process's current working directory using the traversal-laden name, escaping the job workspace `wd`.

### Impact Explanation
Unauthorized file write outside the job workspace on the runner/helper filesystem — matching the scoped impact. Depending on runner/executor privileges and filesystem permissions, this can range from writing arbitrary files in sibling build directories to, in shell/privileged setups, planting files in system paths reachable by the runner process user.

### Likelihood Explanation
Preconditions are realistic and fully attacker-controlled: any pipeline author can produce a custom zip artifact (bypassing GitLab's normal archiver, which itself is not vulnerable) and have it consumed by `artifacts-downloader` in a dependent job. The only gating factor is whether `FF_USE_FASTZIP` is enabled for the runner (which switches the `Zip` format's registered extractor to `fastzip.NewExtractor`, delegating to the `saracen/fastzip` library). Since this repository still registers and ships the legacy zip extractor as the default fallback (registered unconditionally in `commands/helpers/archiver.go`'s imports, and overridden only when the flag is explicitly on), any runner/helper build with the flag disabled or unset is exploitable. This is straightforward and repeatable — no race conditions or special executor configuration required beyond producing a crafted artifact zip.

### Recommendation
Add the same chroot/prefix validation used in `tarzstd_extractor.go` to `helpers/archives/zip_extract.go`'s `extractZipFile`/`ExtractZipArchive`: join `file.Name` against the target extraction directory, compute the absolute path, and reject entries whose resolved path escapes that directory (also reject absolute names and validate symlink targets similarly). Additionally, make `ziplegacy.extractor.Extract` actually use its `dir` field instead of discarding it, so the confinement directory is enforced rather than implicitly relying on `os.Getwd()`.

### Proof of Concept
Go unit test in `helpers/archives` package:
```go
func TestExtractZipArchive_PathTraversal(t *testing.T) {
    tmpOut := t.TempDir()
    wd, _ := os.Getwd()
    os.Chdir(tmpOut)
    defer os.Chdir(wd)

    buf := &bytes.Buffer{}
    zw := zip.NewWriter(buf)
    w, _ := zw.Create("../evil.txt") // traversal entry
    w.Write([]byte("pwned"))
    zw.Close()

    zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    err := ExtractZipArchive(zr)
    require.NoError(t, err) // no rejection today

    // Assertion that SHOULD hold after fix: file must not exist outside tmpOut
    _, statErr := os.Stat(filepath.Join(filepath.Dir(tmpOut), "evil.txt"))
    assert.True(t, os.IsNotExist(statErr), "zip-slip entry escaped extraction directory")
}
```
Expected current behavior: the file is created outside `tmpOut`, demonstrating the bug. After applying the recommended fix (mirroring the `tarzstd` prefix check), `ExtractZipArchive`/`Extract` should return an error and no file should be written outside the target directory.

### Citations

**File:** commands/helpers/artifacts_downloader.go (L91-140)
```go
	wd, err := os.Getwd()
	if err != nil {
		logrus.Fatalln("Unable to get working directory")
	}

	if c.URL == "" {
		logrus.Warningln("Missing URL (--url)")
	}
	if c.Token == "" {
		logrus.Warningln("Missing runner credentials (--token)")
	}
	if c.ID <= 0 {
		logrus.Warningln("Missing build ID (--id)")
	}
	if c.ID <= 0 || c.Token == "" || c.URL == "" {
		logrus.Fatalln("Incomplete arguments")
	}

	// Create temporary file
	file, err := os.CreateTemp(c.StagingDir, "artifacts")
	if err != nil {
		logrus.Fatalln(err)
	}
	_ = file.Close()
	defer func() { _ = os.Remove(file.Name()) }()

	// Download artifacts file
	err = c.doRetry(func(retry int) error {
		return c.download(file.Name(), retry)
	})
	if err != nil {
		logrus.Fatalln(err)
	}

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

**File:** commands/helpers/archiver.go (L10-37)
```go
	// auto-register default archivers/extractors
	_ "gitlab.com/gitlab-org/gitlab-runner/commands/helpers/archive/gziplegacy"
	_ "gitlab.com/gitlab-org/gitlab-runner/commands/helpers/archive/raw"
	_ "gitlab.com/gitlab-org/gitlab-runner/commands/helpers/archive/tarzstd"
	_ "gitlab.com/gitlab-org/gitlab-runner/commands/helpers/archive/ziplegacy"

	"github.com/sirupsen/logrus"
)

func init() {
	// enable fastzip archiver/extractor
	logger := logrus.WithField("name", featureflags.UseFastzip)
	if on := featureflags.IsOn(logger, os.Getenv(featureflags.UseFastzip)); on {
		archive.Register(archive.Zip, fastzip.NewArchiver, fastzip.NewExtractor)

		// The default zstd compressor is fastzip, this is registered via the
		// fastzip implementation (helpers/archive/fastzip).
		//
		// The default zstd decompressor is the legacy zip implementation (helpers/archive/ziplegacy).
		// This intended to allow the default zip implementation to still be able to decompress zstd,
		// even if it is unable to compress it (only fastzip can compress). This also allows the older
		// extraction behaviour to be enabled.
		//
		// Here we're registering the decompress only if FF_USE_FASTZIP is enabled. This overrides
		// the ziplegacy zstd support.
		archive.Register(archive.ZipZstd, nil, fastzip.NewExtractor)
	}
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

**File:** helpers/archives/zip_extract.go (L85-110)
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

	for _, file := range archive.File {
		if err := lchmod(file.Name, file.Mode()); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}

		// Process zip metadata
		if err := processZipExtra(&file.FileHeader); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}
	}

	return nil
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
