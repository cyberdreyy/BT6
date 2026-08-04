Confirmed. `openArchive` defaults `format = archive.Zip` and only switches to `TarZstd` or `Gzip` based on magic bytes; any zip-magic (`PK\x03\x04`) file routes to `archive.Zip`, which resolves to the `ziplegacy` extractor unless `FF_USE_FASTZIP` is enabled (in which case `fastzip.NewExtractor` overrides the `Zip` registration). [1](#0-0) [2](#0-1) 

The `ziplegacy` extractor calls `archives.ExtractZipArchive`, which for every zip entry calls `extractZipFile(file)` and that function uses `file.Name` (attacker-controlled, taken verbatim from the zip central directory) directly in `os.MkdirAll(filepath.Dir(file.Name), ...)`, `os.OpenFile(file.Name, ...)`, `os.Symlink`, and `os.Mkdir` — with no `filepath.Abs` + `dir`-prefix containment check at all, unlike `tarzstd_extractor.go` which explicitly resolves `filepath.Join(e.dir, hdr.Name)` and rejects paths escaping `e.dir`. [3](#0-2) [4](#0-3) 

The `ziplegacy.extractor` even stores the target `dir` field but never uses it during `Extract`. [5](#0-4) 

Go's standard `archive/zip` package does not sanitize or reject `..`-containing names in `zip.File.Name` — it is the extractor's responsibility, which `fastzip` (a third-party library) and `tarzstd`'s hand-rolled logic do, but `ziplegacy`/`archives.ExtractZipArchive` does not.

This confirms the finding.

### Title
Zip-slip path traversal in legacy zip extractor bypasses chroot containment enforced by other extractors - (File: helpers/archives/zip_extract.go)

### Summary
The `ziplegacy` extractor, selected by default for any cache/artifact archive whose magic bytes match a ZIP local file header (and always selected when `FF_USE_FASTZIP` is off), extracts entries via `extractZipFile` using the raw `file.Name` from the zip central directory with no path-containment check, unlike `tarzstd_extractor.go` and the `fastzip`-backed extractor. A malicious cache/artifact zip with entry names like `../../../etc/passwd` or `../../home/gitlab-runner/.ssh/authorized_keys` will be written outside the intended build/cache directory.

### Finding Description
`CacheExtractorCommand.Execute` and `ArtifactsDownloaderCommand.Execute` call `openArchive`, which defaults `format` to `archive.Zip` and only overrides it to `TarZstd`/`Gzip` based on magic-byte sniffing; any attacker-supplied cache/artifact blob starting with the ZIP local-file-header magic (`PK\x03\x04`) resolves to `archive.Zip`. `archive.NewExtractor(archive.Zip, ...)` dispatches to whichever function is registered for `Zip` — `ziplegacy.NewExtractor` unless `FF_USE_FASTZIP` is enabled, in which case `fastzip.NewExtractor` overrides the registration. `ziplegacy.extractor.Extract` opens the zip and calls `archives.ExtractZipArchive(zr)`, which iterates `archive.File` and calls `extractZipFile(file)` for each entry. `extractZipFile` uses `file.Name` (fully attacker-controlled, no validation against `..` or absolute paths) directly in `filepath.Dir(file.Name)` for `os.MkdirAll`, and in `os.OpenFile`/`os.Mkdir`/`os.Symlink` calls for file/dir/symlink entries. There is no `filepath.Abs(filepath.Join(dir, name))` + prefix check as exists in `tarzstd_extractor.go` (lines 57-64), and no such check exists anywhere in `ExtractZipArchive`/`extractZipFile`/`extractZipSymlinkEntry`/`extractZipFileEntry`. Go's standard `archive/zip` reader does not sanitize entry names for path traversal itself, so nothing else in the call path stops it. The `errorIfGitDirectory` check only warns about `.git` directory names and is not a containment mechanism.

### Impact Explanation
An unprivileged pipeline author who controls the cache archive (or, for a compromised/malicious artifact source in the direct-download path, an artifact archive) delivered to `gitlab-runner-helper cache-extractor`/`artifacts-downloader` can write arbitrary files to arbitrary paths on the executor host filesystem (subject to the OS user's permissions), including overwriting files outside the job's working directory (e.g., other projects' cached data on the same host in shell/shared-cache executors, or files under the runner helper's home directory). This is a file-write path-traversal (zip-slip), matching the "File operations must stay within intended build/cache/artifact roots" invariant.

### Likelihood Explanation
Fully attacker-controlled and directly reachable: a job can simply supply a cache archive whose entries contain `../` sequences; no special privileges, admin cooperation, or race condition is needed, only that `FF_USE_FASTZIP` is disabled (which is/was the default behavior for legacy zip decompression path, and the zstd decompressor path in `ziplegacy` is used regardless of the feature flag for `ZipZstd` unless overridden). The bug is deterministically reproducible with a crafted zip file.

### Recommendation
Add the same chroot/path-containment enforcement used in `tarzstd_extractor.go` to `helpers/archives/zip_extract.go`: resolve each entry's target path as `filepath.Abs(filepath.Join(dir, file.Name))`, verify it has `dir+separator` as a prefix (or equals `dir`), and reject the archive/entry otherwise. This requires plumbing the destination `dir` into `ExtractZipArchive`/`extractZipFile` (currently these functions don't take a `dir` parameter at all — `ziplegacy.extractor.dir` is unused).

### Proof of Concept
Go unit test in `helpers/archives`:
```go
func TestExtractZipArchive_RejectsPathTraversal(t *testing.T) {
    tmpDir := t.TempDir()
    outsideDir := t.TempDir()
    zipBuf := &bytes.Buffer{}
    zw := zip.NewWriter(zipBuf)
    // craft entry escaping tmpDir into outsideDir
    relEscape, _ := filepath.Rel(tmpDir, filepath.Join(outsideDir, "evil.txt"))
    w, _ := zw.Create(relEscape)
    w.Write([]byte("pwned"))
    zw.Close()

    origWd, _ := os.Getwd()
    os.Chdir(tmpDir)
    defer os.Chdir(origWd)

    zr, _ := zip.NewReader(bytes.NewReader(zipBuf.Bytes()), int64(zipBuf.Len()))
    err := ExtractZipArchive(zr) // no dir param today -- root cause
    assert.NoError(t, err)

    _, statErr := os.Stat(filepath.Join(outsideDir, "evil.txt"))
    assert.NoError(t, statErr, "file was written outside chroot: zip-slip succeeded")
}
```
Expected (post-fix) assertion: extraction should fail or the file should not be written outside `tmpDir`, matching the behavior already verified for `tarzstd_extractor.go`'s existing test coverage of `"%s cannot be extracted outside of chroot"`.

### Citations

**File:** commands/helpers/artifacts_downloader.go (L143-172)
```go
var (
	zstMagic  = []byte{0x28, 0xB5, 0x2F, 0xFD}
	gzipMagic = []byte{0x1F, 0x8B}
)

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

	fi, err := f.Stat()
	if err != nil {
		f.Close()
		return nil, 0, format, err
	}

	return f, fi.Size(), format, nil
```

**File:** commands/helpers/archiver.go (L19-37)
```go
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L13-33)
```go
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
