### Title
Legacy zip extractor performs no path/symlink containment check, allowing zip-slip during cache extraction - ([File: helpers/archives/zip_extract.go])

### Summary
`ExtractZipArchive` (and its helpers `extractZipFile`, `extractZipFileEntry`, `extractZipDirectoryEntry`, `extractZipSymlinkEntry`) apply `os.Mkdir`/`os.MkdirAll`/`os.OpenFile`/`os.Symlink` directly to `file.Name` taken from the zip entry, with zero validation that the resolved path stays inside the extraction root. This contrasts with `commands/helpers/archive/tarzstd/tarzstd_extractor.go`, which explicitly joins entries against `e.dir` and rejects any path failing `strings.HasPrefix(path, e.dir+separator)`.

### Finding Description
The call chain is:
`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go:26-32` `(*extractor).Extract()` receives an extraction directory `e.dir` from `archive.NewExtractor(format, r, size, dir)` [1](#0-0)  but never uses `e.dir` at all — it just calls `archives.ExtractZipArchive(zr)`. Inside `ExtractZipArchive`, every entry is extracted using the raw `file.Name` string from the archive: `os.Mkdir(file.Name, ...)`, `os.OpenFile(file.Name, ...)`, `os.Symlink(string(data), file.Name)`, with `os.MkdirAll(filepath.Dir(file.Name), 0o777)` to create parents — no `filepath.Clean`, no join with a chroot base, no `HasPrefix` containment check, and no restriction on symlink targets. [2](#0-1) 

Because the "directory to extract into" parameter is discarded, extraction is entirely governed by (a) the entry names embedded in the archive and (b) the process's current working directory when `ExtractZipArchive`/`ExtractZipFile` runs. The `commands/helpers/cache_extractor_test.go`/`archiver_test.go` comment "hack: legacy archiver require being in the correct working dir" confirms this is a known, exercised quirk of this legacy code path, not merely theoretical. [3](#0-2) 

Attacker path: a job controls the contents that end up in its own cache archive (`CacheArchive.Run` invokes `cache-archiver` with `--path` arguments derived from job config) [4](#0-3) , and since the archive file on disk is fully writable by the job before/after the legitimate archiver step, a job with shell access can substitute a hand-crafted malicious zip (entries such as `../../victim/payload` or a symlink entry targeting `/`) for the file that gets uploaded as the cache blob. A later `cache-extractor` invocation — for the same job on retry, a different job/pipeline sharing the same cache key/scope, or a different job sharing the runner host in `run-single`/shared-runner deployments — downloads that blob, detects it as zip via `openArchive`, and (on hosts/builds where the legacy zip package is registered instead of `fastzip`, e.g., platforms lacking the fastzip/mmap-based implementation) extracts it through this unguarded code path in the process's working directory, which `commands/helpers/cache_extractor.go` sets from `os.Getwd()` at the time `cache-extractor` runs. [5](#0-4)  No overwrite guard, path validation, or chroot check exists to stop `../` traversal or an absolute/symlink escape in this code path, unlike the tarzstd extractor's explicit containment check. [6](#0-5) 

Note: I could not fully confirm, from the available index, the exact conditions under which the `ziplegacy` package (versus `fastzip`, whose extractor correctly honors `e.dir` at `commands/helpers/archive/fastzip/zip_fastzip_extractor.go:33-46`) is the one actually registered/selected for the `"zip"` format at runtime — `archivers`/`extractors` registration happens via `archive.Register()` and only one implementation can be active per `Format` value. [7](#0-6)  This is a real gap in the vulnerable code regardless, but its live exploitability depends on that registration/build-tag detail, which should be verified directly in the repository (e.g., in `commands/helpers/archiver.go`) before treating this as remotely triggerable on all builds.

### Impact Explanation
If the legacy zip extractor is reachable for cache extraction on any supported build/architecture, a job can escape its own `CacheDir`/`BuildDir` extraction root during cache restore and write or symlink files elsewhere on the runner host filesystem accessible to the runner process — violating the invariant that "file operations must stay within intended build/cache/artifact roots," and potentially corrupting or planting files into another job's workspace on hosts where builds/caches share the same filesystem (e.g., `run-single` or misconfigured shared runners without per-job filesystem isolation).

### Likelihood Explanation
Preconditions: (1) the runner extracts caches using the legacy zip implementation rather than `fastzip` (architecture/build-tag dependent — unconfirmed from available context), and (2) the job has enough control to substitute a hand-crafted zip for its own cache archive file before upload (trivial with shell executor access to the job's own working directory). Given those preconditions, the exploit is fully repeatable and deterministic — the vulnerable code performs no checks at all.

### Recommendation
Add the same containment logic used in `tarzstd_extractor.go` to `helpers/archives/zip_extract.go`: resolve each `file.Name` against the extraction root passed into `ExtractZipArchive`/`extractZipFile` via `filepath.Join` + `filepath.Abs`, reject any entry whose resolved path does not have the root as a prefix, and validate symlink targets (`extractZipSymlinkEntry`) the same way before calling `os.Symlink`. Also make `ziplegacy.extractor.Extract` actually pass and use `e.dir` instead of discarding it.

### Proof of Concept
```go
func TestExtractZipArchive_PathTraversal(t *testing.T) {
    root := t.TempDir()
    outside := filepath.Join(root, "..", "victim-payload")
    _ = os.Remove(outside)

    buf := new(bytes.Buffer)
    zw := zip.NewWriter(buf)
    w, _ := zw.Create("../victim-payload")
    _, _ = w.Write([]byte("pwned"))
    _ = zw.Close()

    orig, _ := os.Getwd()
    defer os.Chdir(orig)
    _ = os.Chdir(root)

    zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    _ = archives.ExtractZipArchive(zr)

    _, err := os.Stat(outside)
    assert.NoError(t, err, "expected traversal file to exist outside extraction root, confirming the escape")
    _ = os.Remove(outside)
}
```
Expected (buggy) result: the file is created at `outside`, outside the intended extraction root, proving the missing containment check. A fixed implementation should return an error instead and never create `outside`.

### Citations

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L20-32)
```go
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
```

**File:** helpers/archives/zip_extract.go (L22-83)
```go
func extractZipSymlinkEntry(file *zip.File) (err error) {
	var data []byte
	in, err := file.Open()
	if err != nil {
		return err
	}
	defer func() { _ = in.Close() }()

	data, err = io.ReadAll(in)
	if err != nil {
		return err
	}

	// Remove symlink before creating a new one, otherwise we can error that file does exist
	_ = os.Remove(file.Name)
	err = os.Symlink(string(data), file.Name)
	return
}

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

**File:** commands/helpers/archiver_test.go (L68-69)
```go
		// hack: legacy archiver require being in the correct working dir
		_ = os.Chdir(out)
```

**File:** functions/concrete/run/stages/cache_archive.go (L45-61)
```go
	archiveFile := s.archivePath(e)

	args := []string{
		"cache-archiver",
		"--file", archiveFile,
		"--timeout", strconv.Itoa(s.Timeout),
	}

	if s.AlternateKey != "" && s.AlternateKey != s.Key {
		args = append(args, "--alternate-file", s.alternateArchivePath(e))
	}

	if s.MaxUploadedArchiveSize > 0 {
		args = append(args, "--max-uploaded-archive-size", strconv.FormatInt(s.MaxUploadedArchiveSize, 10))
	}

	args = append(args, archiverArgs...)
```

**File:** commands/helpers/cache_extractor.go (L618-663)
```go
func (c *CacheExtractorCommand) Execute(cliContext *cli.Context) {
	log.SetRunnerFormatter()

	c.normalizeExtractorArgs()
	if err := validateCacheTransferTuning(c.TransferBufferSize, c.ChunkSize, c.Concurrency); err != nil {
		logrus.Fatalln(err)
	}

	wd, err := os.Getwd()
	if err != nil {
		logrus.Fatalln("Unable to get working directory")
	}

	if c.File == "" {
		warningln("Missing cache file")
	}

	if c.URL != "" || c.GoCloudURL != "" {
		err := c.doRetry(c.download)
		if err != nil {
			warningln(err)
		}
	} else {
		logrus.Infoln(
			"No URL provided, cache will not be downloaded from shared cache server. " +
				"Instead a local version of cache will be extracted.")
	}

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

**File:** commands/helpers/archive/archive.go (L65-97)
```go
// Register registers a new archiver, overriding the archiver and/or extractor
// for the format provided.
func Register(
	format Format,
	archiver NewArchiverFunc,
	extractor NewExtractorFunc,
) (
	prevArchiver NewArchiverFunc,
	prevExtractor NewExtractorFunc,
) {
	if archiver != nil {
		prevArchiver = archivers[format]
		archivers[format] = archiver
	}
	if extractor != nil {
		prevExtractor = extractors[format]
		extractors[format] = extractor
	}
	return
}

// NewArchiver returns a new Archiver of the specified format.
//
// The archiver will ensure that files to be archived are children of the
// directory provided.
func NewArchiver(format Format, w io.Writer, dir string, level CompressionLevel) (Archiver, error) {
	fn := archivers[format]
	if fn == nil {
		return nil, fmt.Errorf("%q format: %w", format, ErrUnsupportedArchiveFormat)
	}

	return fn(w, dir, level)
}
```
