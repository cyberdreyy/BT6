### Title
Zip artifact/cache extraction writes files outside the extraction root via `../` path traversal - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipFile`/`extractZipFileEntry`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry` use `file.Name` from the zip header directly for `os.MkdirAll`, `os.OpenFile`, `os.Mkdir`, and `os.Symlink` with no path-containment check. Unlike the tar+zstd extractor (`commands/helpers/archive/tarzstd/tarzstd_extractor.go`), which joins entry names against a fixed root and rejects any resulting path outside that root, the zip path performs no such validation, so a crafted zip with `../` segments in entry names can write files outside the intended cache/artifact extraction directory.

### Finding Description
In `helpers/archives/zip_extract.go`:
- `extractZipFile` (lines 61-83) calls `os.MkdirAll(filepath.Dir(file.Name), 0o777)` directly on the raw zip entry name.
- `extractZipFileEntry` (lines 41-59) calls `os.OpenFile(file.Name, ...)` directly.
- `extractZipDirectoryEntry` and `extractZipSymlinkEntry` similarly use `file.Name` unmodified for `os.Mkdir`/`os.Symlink`.

None of these functions clean, join against a root, or validate that `file.Name` stays within an intended directory. The only existing check, `errorIfGitDirectory` (in `path_check_helper.go`), only rejects paths whose first segment is `.git`; it does not detect or reject `../` traversal. [1](#0-0) 

Compare this to `commands/helpers/archive/tarzstd/tarzstd_extractor.go`, which explicitly computes `path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))` and then enforces `strings.HasPrefix(path, e.dir+string(filepath.Separator))`, returning an error ("cannot be extracted outside of chroot") if the resolved path escapes the root directory. [2](#0-1)  The zip extraction path has no equivalent guard: `extractZipFile` unconditionally does `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and dispatches to file/dir/symlink handlers that operate directly on `file.Name`. [3](#0-2) 

Furthermore, `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`'s `extractor.Extract` accepts a `dir` field intended to scope extraction, but it never uses `e.dir` when calling `archives.ExtractZipArchive(zr)` — the directory parameter is stored but ignored entirely. [4](#0-3)  This confirms the zip code path relies solely on the process's current working directory for containment, with zero validation against `../` segments in entry names.

Exploit flow: an unprivileged pipeline author controls the contents of files placed under `artifacts:paths` or `cache:paths`, which are zip-compressed by the runner and later downloaded/extracted by another job (or re-extracted from cache) using this same `ExtractZipArchive`/`ExtractZipFile` code. By crafting or influencing a zip archive with entries such as `../../shared-vol/payload` (feasible if a malicious cache/artifact zip is supplied, e.g. via a compromised/attacker-influenced cache blob or a job configured to unpack an externally fetched zip with this library), the entry's `file.Name` is used unmodified, causing `os.MkdirAll`/`os.OpenFile` to write outside the intended extraction root — e.g. into a directory shared with a service container via a mounted volume.

### Impact Explanation
If exploited, an unprivileged job could write attacker-controlled files to paths outside its own working directory — including any writable path reachable relative to the process's CWD (e.g. a mounted shared volume used by service containers), enabling a job to plant a payload that a helper/service container with different identity or privilege later executes or reads. This directly violates the workspace-confinement invariant: file operations must stay within intended build/cache/artifact roots.

### Likelihood Explanation
The core write primitive (`extractZipFile`/`extractZipFileEntry`) has zero path-containment validation, so the mechanical bug is real and trivially reachable by any code path that calls `archives.ExtractZipArchive`/`ExtractZipFile` on attacker-influenced zip content. However, I could not fully confirm within the available index which production caller (artifact/cache download flow) invokes this exact zip path with attacker-supplied cache-server content versus GitLab-server-validated artifact zips, nor whether an outer wrapper (e.g. in `commands/helpers/cache_extractor.go` or `artifacts_downloader.go`) performs its own path-restriction/`Chdir`-based confinement before calling into `archives`. The `ziplegacy` extractor's unused `dir` field strongly suggests no such external confinement exists for that call path, but full confirmation would require reading those caller files, which were not retrievable in full in this session.

### Recommendation
Add the same containment check used in the tar+zstd extractor: resolve each `file.Name` against a fixed extraction root via `filepath.Join`/`filepath.Abs`, reject entries whose cleaned path is not prefixed by that root (or equals it), and reject/clean `..` segments before any `os.MkdirAll`/`os.OpenFile`/`os.Mkdir`/`os.Symlink` call in `extractZipFile`, `extractZipFileEntry`, `extractZipDirectoryEntry`, and `extractZipSymlinkEntry`. Also wire the unused `dir` field in `ziplegacy.extractor` into `ExtractZipArchive` so extraction is confined to that directory, matching `fastzip`/`tarzstd` extractor behavior.

### Proof of Concept
```go
// helpers/archives/zip_extract_test.go
func TestExtractZipFile_PathTraversalRejected(t *testing.T) {
    testInWorkDir(t, func(t *testing.T, fileName string) {
        f, err := os.Create(fileName)
        require.NoError(t, err)
        defer f.Close()

        zw := zip.NewWriter(f)
        w, err := zw.Create("../escaped_payload.txt")
        require.NoError(t, err)
        _, err = io.WriteString(w, "malicious content")
        require.NoError(t, err)
        require.NoError(t, zw.Close())
        f.Close()

        err = ExtractZipFile(fileName)
        // Expect either an error or that the file was NOT written outside the work dir.
        _, statErr := os.Stat(filepath.Join(filepath.Dir(mustGetwd(t)), "escaped_payload.txt"))
        assert.True(t, os.IsNotExist(statErr), "entry escaped extraction root")
    })
}
```
Expected current (buggy) behavior: no error is returned and `escaped_payload.txt` is created one directory above the working directory, proving the traversal. After the fix, the function should either return an error for the malicious entry or normalize it to remain inside the root, and the assertion above should pass.

### Citations

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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L12-32)
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
```
