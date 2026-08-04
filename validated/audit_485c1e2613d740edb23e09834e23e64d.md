## Confirmed: no path-check exists for the zip extractor - format-dependent path traversal

### Title
Zip extractor lacks path-confinement check present in tar+zstd extractor, enabling path traversal via crafted entry names - (File: helpers/archives/zip_extract.go)

### Summary
The zip extraction path (`extractZipFile` and its helpers in `helpers/archives/zip_extract.go`, invoked via `ExtractZipArchive`/`ExtractZipFile`, which is called by `ziplegacy.extractor.Extract`) writes files using `file.Name` directly with no join against, or prefix-check against, the target extraction directory. In contrast, `tarzstd.extractor.Extract` explicitly computes `filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects any entry whose resolved path does not have `e.dir` as a prefix. Because zip/cache/artifact extraction chooses format dynamically, an attacker who controls the archive contents (e.g. cache/artifact upload) and can select or default to the `zip` format bypasses the containment enforced for `tarzstd`.

### Finding Description
`extractZipFile` (helpers/archives/zip_extract.go:61-83) dispatches to `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, and `extractZipFileEntry`, all of which perform `os.Mkdir(file.Name, ...)`, `os.Symlink(data, file.Name)`, and `os.OpenFile(file.Name, ...)` directly on the zip entry's raw `Name` field [1](#0-0) . There is no `filepath.Join` against an extraction root and no prefix/containment check anywhere in this file — the only validation present is `errorIfGitDirectory`, which merely blocks entries whose first path component is `.git` and is unrelated to traversal [2](#0-1) . The `ziplegacy` extractor, which implements the `archive.Extractor` interface with a `dir` field just like `tarzstd`, receives `e.dir` but never uses it when calling `archives.ExtractZipArchive(zr)` [3](#0-2) ; extraction relies entirely on the process's current working directory rather than any explicit chroot-style validation.

By contrast, `tarzstd.extractor.Extract` computes the absolute destination path from `e.dir` and `hdr.Name`, then explicitly enforces `strings.HasPrefix(path, e.dir+string(filepath.Separator))` (or exact equality to `e.dir`), returning an error `"%s cannot be extracted outside of chroot (%s)"` for any header whose resolved path escapes the directory [4](#0-3) .

`archive.NewExtractor` dispatches to whichever extractor is registered for the selected `Format` (`Zip`, `ZipZstd`, `TarZstd`) [5](#0-4) , and `CacheExtractorCommand.Execute` selects the format from the downloaded/local archive and constructs the extractor with the process working directory as `dir` [6](#0-5) . This confirms the same `dir`-scoped extraction contract is shared across formats, but only the tar+zstd implementation enforces it.

An attacker who controls the archive content presented to the extractor (cache archive, artifacts) can craft a zip entry named `../../../../tmp/pwned` (or an absolute path). When `ziplegacy`/`fastzip`-registered zip format is used, `extractZipFile` will call `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and then create/write the file at the traversal-resolved location relative to CWD, with no rejection — succeeding where the identical entry against `tarzstd` would return `"...cannot be extracted outside of chroot..."`.

Note: `fastzip`'s extractor delegates to the third-party `github.com/saracen/fastzip` library rather than to `helpers/archives/zip_extract.go` [7](#0-6) ; its own traversal protections were not verified in this audit and are out of scope for this specific file-level finding, but `ziplegacy` (registered for `zip`/`zipzstd` per `Register` calls) is directly and unambiguously vulnerable via `helpers/archives/zip_extract.go`.

### Impact Explanation
Any job or pipeline component that controls the contents of a cache or artifacts zip archive extracted by the runner (via `ziplegacy`) can write arbitrary files outside the intended build directory on the host running the job (relative to whatever the current working directory happens to be at extraction time), via `os.Mkdir`/`os.OpenFile`/`os.Symlink` following a `../` traversal in the entry name. This violates the "file operations must stay within intended build/cache/artifact roots" invariant and is a format-dependent bypass: the same job config using `tarzstd` would be blocked, but `zip`/`zipzstd` (via `ziplegacy`) is not.

### Likelihood Explanation
No special privileges are needed beyond the ability to control cache/artifact content and the selected archive format (job config or default), both of which are attacker-controlled per the stated preconditions. The bug is deterministic and 100% repeatable: any zip archive containing a traversal-crafted entry name will always bypass path confinement in `helpers/archives/zip_extract.go`, since there is no code path that performs any check.

### Recommendation
In `helpers/archives/zip_extract.go`, thread the extraction root (`dir`) through `ExtractZipArchive`/`ExtractZipFile`/`extractZipFile` and its helpers, mirroring `tarzstd`: compute `filepath.Abs(filepath.Join(dir, file.Name))` for every entry and reject any resolved path that is not prefixed by `dir` (or equal to it), before performing `Mkdir`/`OpenFile`/`Symlink`/`lchmod`. Update `ziplegacy.extractor.Extract` to pass `e.dir` into this validated path-join logic instead of relying on `ExtractZipArchive(zr)` operating implicitly on the process CWD.

### Proof of Concept
Go test in `helpers/archives` (or `commands/helpers/archive/ziplegacy`):
```go
func TestZipExtract_PathTraversal(t *testing.T) {
    dir := t.TempDir()
    outsideMarker := filepath.Join(dir, "..", "pwned_by_zip")

    // Build a zip in-memory with a single entry named "../pwned_by_zip"
    buf := &bytes.Buffer{}
    zw := zip.NewWriter(buf)
    w, _ := zw.Create("../pwned_by_zip")
    _, _ = w.Write([]byte("owned"))
    _ = zw.Close()

    // Extract via ziplegacy.NewExtractor with dir as destination root
    ext, _ := ziplegacy.NewExtractor(bytes.NewReader(buf.Bytes()), int64(buf.Len()), dir)
    err := ext.Extract(context.Background())

    // Compare with tarzstd using identical entry name
    // ... build equivalent tar+zstd archive with header.Name = "../pwned_by_zip"
    // tarzstdExt.Extract(ctx) should return error containing "cannot be extracted outside of chroot"

    assert.NoError(t, err, "zip extractor should have failed containment but did not")
    _, statErr := os.Stat(outsideMarker)
    assert.NoError(t, statErr, "file was written outside extraction dir")
}
```
Expected: the tar+zstd extractor call errors with `"cannot be extracted outside of chroot"`, while the zip extractor call succeeds and the file `pwned_by_zip` is found written one directory above the intended extraction root — demonstrating the differential isolation gap.

### Citations

**File:** helpers/archives/zip_extract.go (L12-59)
```go
func extractZipDirectoryEntry(file *zip.File) (err error) {
	err = os.Mkdir(file.Name, file.Mode().Perm())

	// The "directory does exist" error is not an error for us
	if os.IsExist(err) {
		err = nil
	}
	return
}

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
```

**File:** helpers/archives/path_check_helper.go (L13-19)
```go
func isPathAGitDirectory(path string) bool {
	parts := strings.Split(filepath.Clean(path), string(filepath.Separator))
	if len(parts) > 0 && parts[0] == ".git" {
		return true
	}
	return false
}
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L26-32)
```go
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
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

**File:** commands/helpers/archive/fastzip/zip_fastzip_extractor.go (L33-46)
```go
func (e *extractor) Extract(ctx context.Context) error {
	opts, err := getExtractorOptionsFromEnvironment()
	if err != nil {
		return err
	}

	extractor, err := fastzip.NewExtractorFromReader(e.r, e.size, e.dir, opts...)
	if err != nil {
		return err
	}
	defer extractor.Close()

	return extractor.Extract(ctx)
}
```
