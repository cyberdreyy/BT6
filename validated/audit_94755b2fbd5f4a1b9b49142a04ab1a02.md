### Title
`errorIfGitDirectory` warning is not invoked by gzip/tarzstd/fastzip archivers/extractors, allowing format selection to skip the only existing `.git`-poisoning signal - ([File: helpers/archives/path_check_helper.go])

### Summary
`errorIfGitDirectory` (and the corresponding `printGitArchiveWarning`) is only called from `CreateZipArchive`/`ExtractZipArchive` in `helpers/archives/zip_create.go` and `helpers/archives/zip_extract.go`. The `gzip`, `tarzstd`, and `zipzstd`(fastzip) archivers/extractors registered in `commands/helpers/archive/archive.go`'s `archivers`/`extractors` maps never call it, so a job/config selecting those formats for cache/artifacts produces zero log signal when `.git` paths are archived or extracted.

### Finding Description
`NewArchiver`/`NewExtractor` in `commands/helpers/archive/archive.go` dispatch purely by `Format` key through the `archivers`/`extractors` maps [1](#0-0) . Each format's implementation is self-contained and independently registers itself via `init()`:

- `ziplegacy/zip_legacy_archiver.go` delegates to `archives.CreateZipArchive`/legacy zip extractor, which do call `errorIfGitDirectory` and `printGitArchiveWarning` [2](#0-1) [3](#0-2) .
- `gziplegacy/gzip_legacy_archiver.go` delegates to `archives.CreateGzipArchive`, which contains no `.git` check at all [4](#0-3) .
- `fastzip/zip_fastzip_archiver.go` (format `zipzstd`) uses the third-party `fastzip.NewArchiver` directly and never calls `errorIfGitDirectory` [5](#0-4) .
- `tarzstd/tarzstd_archiver.go` and `tarzstd/tarzstd_extractor.go` implement their own tar+zstd read/write loops with path-traversal (chroot) checks but no `.git` check anywhere [6](#0-5) [7](#0-6) .
- `raw/raw_archiver.go` is single-file only and also has no such check [8](#0-7) .

The check itself, `errorIfGitDirectory`, only tests whether the top-level path component equals `.git` and is not itself an error/abort mechanism — callers (`zip_create.go`/`zip_extract.go`) use it merely to decide whether to print a `logrus.Warn` via `printGitArchiveWarning`; it never blocks the archive/extract operation [9](#0-8) . So even for zip, this is purely advisory logging, not an enforced protection. For gzip/tarzstd/fastzip/raw, that advisory logging is entirely absent.

Format selection for cache/artifacts is controlled by CI/runner config (`CacheType`/`ArtifactFormat` style fields feeding into `archive.Format`) — an unprivileged pipeline/job author who can influence artifact/cache paths and format can choose `tarzstd`, `gzip`, or `zipzstd` to avoid any `.git`-related log line while still archiving/extracting a `.git` directory tree.

### Impact Explanation
The impact is confined to loss of the pre-existing advisory warning log for zip. Since `errorIfGitDirectory` never blocks the operation for any format (including zip), the actual archive/extract of `.git` content proceeds identically across all formats. The only observable difference is: zip logs a warning ("Part of .git directory is on the list of files to archive/extract... This may introduce unexpected problems"), whereas gzip/tarzstd/zipzstd/raw do not. This is a detection/observability gap, not a new capability to write/read outside the intended directory — path-traversal-style protections in tarzstd (`filepath.Abs`+`strings.HasPrefix` chroot check) are separate and remain in effect for that format.

### Likelihood Explanation
Trivially reachable: any job whose cache/artifact configuration lets Runner choose a non-zip archive format (e.g. via `FF_USE_FASTZIP`/cache `Compression`/`ArtifactFormat` settings or Runner config defaults) will simply never hit the zip-specific warning code path. No special privileges beyond controlling job/cache/artifact configuration are needed.

### Recommendation
Move the `.git` path check (and `printGitArchiveWarning`) into a shared helper invoked from every archiver/extractor implementation (`gzip_create.go`, `fastzip`'s `Archive`, `tarzstd`'s `Archive`/`Extract`, and `raw`), or better, centralize it once in `commands/helpers/archive/archive.go`'s `NewArchiver`/`NewExtractor` wrapper so it applies uniformly regardless of format, per the stated invariant that git-directory protection must apply uniformly.

### Proof of Concept
Go test in `commands/helpers/archive` package:
```go
func TestGitDirectoryWarningAcrossFormats(t *testing.T) {
    for _, format := range []archive.Format{archive.Zip, archive.Gzip, archive.TarZstd, archive.ZipZstd} {
        t.Run(string(format), func(t *testing.T) {
            var buf bytes.Buffer
            hook := test.NewGlobal() // logrus test hook
            files := map[string]os.FileInfo{".git/config": fakeFileInfo(".git/config")}
            a, err := archive.NewArchiver(format, &buf, ".", archive.DefaultCompression)
            require.NoError(t, err)
            _ = a.Archive(context.Background(), files)
            found := false
            for _, e := range hook.AllEntries() {
                if strings.Contains(e.Message, ".git directory") {
                    found = true
                }
            }
            assert.True(t, found, "expected .git warning for format %s", format)
        })
    }
}
```
Expected result today: assertion passes only for `Zip`; fails for `Gzip`, `TarZstd`, `ZipZstd`, confirming the inconsistency.

### Citations

**File:** commands/helpers/archive/archive.go (L90-109)
```go
func NewArchiver(format Format, w io.Writer, dir string, level CompressionLevel) (Archiver, error) {
	fn := archivers[format]
	if fn == nil {
		return nil, fmt.Errorf("%q format: %w", format, ErrUnsupportedArchiveFormat)
	}

	return fn(w, dir, level)
}

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

**File:** helpers/archives/zip_create.go (L85-103)
```go
func CreateZipArchive(w io.Writer, fileNames []string) error {
	tracker := newPathErrorTracker()

	archive := zip.NewWriter(w)
	defer func() { _ = archive.Close() }()

	for _, fileName := range fileNames {
		if err := errorIfGitDirectory(fileName); tracker.actionable(err) {
			printGitArchiveWarning("archive")
		}

		err := createZipEntry(archive, fileName)
		if err != nil {
			return err
		}
	}

	return nil
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

**File:** helpers/archives/gzip_create.go (L46-63)
```go
func CreateGzipArchive(w io.Writer, fileNames []string) error {
	for _, fileName := range fileNames {
		fi, err := os.Lstat(fileName)
		if os.IsNotExist(err) {
			logrus.Warningln("File ignored:", err)
			continue
		} else if err != nil {
			return err
		}

		err = writeGzipFile(w, fileName, fi)
		if err != nil {
			return err
		}
	}

	return nil
}
```

**File:** commands/helpers/archive/fastzip/zip_fastzip_archiver.go (L72-113)
```go
func (a *archiver) Archive(ctx context.Context, files map[string]os.FileInfo) error {
	tmpDir, err := os.MkdirTemp(os.Getenv(archiverStagingDir), "fastzip")
	if err != nil {
		return fmt.Errorf("fastzip archiver unable to create temporary directory: %w", err)
	}
	defer os.RemoveAll(tmpDir)

	opts, err := getArchiverOptionsFromEnvironment()
	if err != nil {
		return err
	}

	opts = append(opts, fastzip.WithStageDirectory(tmpDir))
	if a.level == archive.FastestCompression {
		opts = append(opts, fastzip.WithArchiverMethod(zip.Store))
	}

	if a.zstd {
		opts = append(opts, fastzip.WithArchiverMethod(zstd.ZipMethodWinZip))
	}

	fa, err := fastzip.NewArchiver(a.w, a.dir, opts...)
	if err != nil {
		return err
	}

	if a.level != archive.FastestCompression {
		if a.zstd {
			fa.RegisterCompressor(zstd.ZipMethodWinZip, fastzip.ZstdCompressor(zstdLevels[a.level]))
		} else {
			fa.RegisterCompressor(zip.Deflate, fastzip.FlateCompressor(flateLevels[a.level]))
		}
	}

	err = fa.Archive(ctx, files)

	if cerr := fa.Close(); err == nil && cerr != nil {
		return cerr
	}

	return err
}
```

**File:** commands/helpers/archive/tarzstd/tarzstd_archiver.go (L62-104)
```go
	for _, name := range sorted {
		fi := files[name]
		if fi.Mode()&irregularModes != 0 {
			continue
		}

		path, err := filepath.Abs(name)
		if err != nil {
			return err
		}
		if !strings.HasPrefix(path, a.dir+string(filepath.Separator)) && path != a.dir {
			return fmt.Errorf("%s cannot be archived from outside of chroot (%s)", name, a.dir)
		}

		rel, err := filepath.Rel(a.dir, path)
		if err != nil {
			return err
		}

		if ctx.Err() != nil {
			return ctx.Err()
		}

		var link string
		if fi.Mode()&os.ModeSymlink != 0 {
			link, err = os.Readlink(path)
			if err != nil {
				return err
			}
		}

		hdr, err := tar.FileInfoHeader(fi, link)
		if err != nil {
			return err
		}
		hdr.Name = rel
		if fi.IsDir() {
			hdr.Name += "/"
		}

		if err := tw.WriteHeader(hdr); err != nil {
			return err
		}
```

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L43-105)
```go
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}

		fi := hdr.FileInfo()
		if fi.Mode()&irregularModes != 0 {
			continue
		}

		var path string
		path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))
		if err != nil {
			return err
		}
		if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir {
			return fmt.Errorf("%s cannot be extracted outside of chroot (%s)", path, e.dir)
		}

		if err := os.MkdirAll(filepath.Dir(path), 0777); err != nil {
			return err
		}

		if ctx.Err() != nil {
			return ctx.Err()
		}

		switch {
		case fi.Mode()&os.ModeSymlink != 0:
			deferred[path] = hdr
			continue

		case fi.Mode().IsDir():
			deferred[path] = hdr

			err := os.Mkdir(path, 0777)
			if err != nil && !os.IsExist(err) {
				return err
			}

		case fi.Mode().IsRegular():
			f, err := os.Create(path)
			if err != nil {
				return err
			}

			if _, err := io.Copy(f, tr); err != nil {
				f.Close()
				return err
			}
			if err := f.Close(); err != nil {
				return err
			}

			if err := e.updateFileMetadata(path, hdr); err != nil {
				return err
			}
		}
	}
```

**File:** commands/helpers/archive/raw/raw_archiver.go (L35-52)
```go
func (a *archiver) Archive(ctx context.Context, files map[string]os.FileInfo) error {
	if len(files) > 1 {
		return ErrTooManyRawFiles
	}

	for pathname := range files {
		f, err := os.Open(pathname)
		if err != nil {
			return err
		}
		defer f.Close()

		_, err = io.Copy(a.w, f)
		return err
	}

	return nil
}
```

**File:** helpers/archives/path_check_helper.go (L21-36)
```go
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

func printGitArchiveWarning(operation string) {
	logrus.Warn(fmt.Sprintf("Part of .git directory is on the list of files to %s", operation))
	logrus.Warn("This may introduce unexpected problems")
}
```
