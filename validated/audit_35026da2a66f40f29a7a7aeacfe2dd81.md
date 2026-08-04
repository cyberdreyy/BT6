### Title
Zip-slip path traversal in legacy zip extractor writes files outside artifact/cache root - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipFile` (and its helpers `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, `extractZipFileEntry`) in `helpers/archives/zip_extract.go` write to `file.Name` verbatim with no confinement check, unlike the tar+zstd extractor which validates `strings.HasPrefix(path, e.dir+string(filepath.Separator))`. The legacy zip extraction path (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`) additionally discards the `dir` root it receives, so a malicious `../`-prefixed entry name in a downloaded artifact/cache zip results in file creation/overwrite/symlink creation anywhere the runner process can write.

### Finding Description
`ArtifactsDownloaderCommand.Execute` / `CacheExtractorCommand.Execute` call `archive.NewExtractor(format, f, size, wd)` where `format` is chosen by `openArchive` based on file magic bytes; for a normal/legacy zip file the format resolves to `archive.Zip`, which is registered to the `ziplegacy` extractor (`commands/helpers/artifacts_downloader.go:125-141`, `commands/helpers/cache_extractor.go:646-664`). [1](#0-0) 

`ziplegacy.extractor.Extract` receives `e.dir` (the intended extraction root, `wd`) but never uses it — it just opens the zip reader and calls `archives.ExtractZipArchive(zr)`: [2](#0-1) 

`ExtractZipArchive` iterates `archive.File` and calls `extractZipFile(file)` for each entry, which uses `file.Name` directly, unmodified, unjoined with any root directory: [3](#0-2) 

The only pre-write check is `errorIfGitDirectory`, which only rejects paths beginning with `.git` — it does not validate for `..` traversal or absolute paths: [4](#0-3) 

Compare this to the tar+zstd extractor, which resolves the absolute path via `filepath.Join(e.dir, hdr.Name)` and explicitly rejects any path that doesn't stay under `e.dir`: [5](#0-4) 

No equivalent join-and-validate step exists in `extractZipFile`/`ExtractZipArchive`. Since `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and `os.OpenFile(file.Name, ...)` operate on whatever raw path is embedded in the zip header, a header name like `../../../../tmp/pwned` (or an absolute path) escapes the intended extraction directory. `extractZipSymlinkEntry` similarly calls `os.Symlink(string(data), file.Name)` on the unvalidated name, allowing arbitrary symlink creation as well.

### Impact Explanation
An attacker who controls the content of a job artifact or cache archive (e.g., a job in the same pipeline that crafts a zip and uploads it as an artifact/cache, later downloaded/extracted by another job or by the runner itself when the cache/artifact format resolves to plain zip rather than tarzstd) can write or overwrite arbitrary files anywhere the runner process has filesystem write permission, and can create symlinks pointing outside the extraction root. This is a file write/overwrite outside the artifact/cache root, matching the scoped impact ("unauthorized file write/overwrite outside artifact/cache root, potentially runner host file corruption").

### Likelihood Explanation
Feasible and repeatable whenever the artifact/cache download resolves to the `zip` format (legacy path), since `openArchive` only special-cases zstd and gzip magic bytes and defaults to `Zip` otherwise — i.e., any zip archive (the default/most common artifact format) triggers the vulnerable `ziplegacy` extractor. No special runner or admin configuration is required; a normal pipeline author can control artifact/cache contents (e.g., via a `zip` command in their job script producing a crafted archive, or by directly assembling a malicious zip and having it treated as a build artifact). The attack is fully within the "attacker action/data -> trigger -> bad state" path with no privileged access needed.

### Recommendation
Add path confinement validation in `helpers/archives/zip_extract.go`, mirroring the tarzstd extractor: join each `file.Name` with the target extraction root, resolve to an absolute/cleaned path, and reject any entry whose resolved path does not have the root directory (plus separator) as a prefix, before any `os.Mkdir`, `os.OpenFile`, or `os.Symlink` call. Additionally, thread the `dir` parameter that `ziplegacy.extractor` already receives through to `ExtractZipArchive`/`extractZipFile` instead of discarding it, and reject symlink targets that escape the root as well.

### Proof of Concept
```go
func TestExtractZipArchive_ZipSlip(t *testing.T) {
    tmpDir := t.TempDir()
    outside := filepath.Join(os.TempDir(), "pwned-zipslip-test")
    defer os.Remove(outside)

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    w, err := zw.Create("../../../../tmp/pwned-zipslip-test")
    require.NoError(t, err)
    _, err = w.Write([]byte("owned"))
    require.NoError(t, err)
    require.NoError(t, zw.Close())

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)

    wd, _ := os.Getwd()
    require.NoError(t, os.Chdir(tmpDir))
    defer os.Chdir(wd)

    err = archives.ExtractZipArchive(zr)
    require.NoError(t, err)

    // Expect the file NOT to exist outside tmpDir; current code fails this assertion.
    _, statErr := os.Stat(outside)
    assert.True(t, os.IsNotExist(statErr), "zip-slip file was written outside extraction root: %s", outside)
}
```
Expected with current code: the assertion fails because `pwned-zipslip-test` is created outside `tmpDir` (e.g., in `os.TempDir()`), proving the zip-slip write.

### Citations

**File:** commands/helpers/artifacts_downloader.go (L125-141)
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
}
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L13-32)
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
