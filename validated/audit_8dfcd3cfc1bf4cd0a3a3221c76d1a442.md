### Title
Zip Slip path traversal in legacy zip extractor allows writes outside job workspace - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipFile`, `extractZipFileEntry`, `extractZipDirectoryEntry`, and `extractZipSymlinkEntry` in `helpers/archives/zip_extract.go` use `zip.File.Name` verbatim as a filesystem path with no sanitization for `..` segments or absolute/drive-letter paths, and `ExtractZipArchive` never confines writes to a caller-supplied root directory. This is a classic Zip Slip vulnerability reachable from cache/artifact extraction via the legacy zip extractor.

### Finding Description
`ExtractZipArchive` iterates `archive.File` and calls `extractZipFile(file)` for every entry [1](#0-0) . The only check performed is `errorIfGitDirectory`, which only rejects paths whose first path component (after `filepath.Clean`) is `.git`; it does not reject `..` traversal or absolute paths [2](#0-1) .

`extractZipFile` then does `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and dispatches to `extractZipDirectoryEntry` (`os.Mkdir(file.Name, ...)`), `extractZipSymlinkEntry` (`os.Symlink(...,file.Name)`), or `extractZipFileEntry` (`os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, ...)`) — all operating directly on the attacker-controlled `file.Name` string, with no `filepath.Clean`, no rejection of `..`, and critically **no confinement to any extraction root at all** — the function signature takes no destination directory [3](#0-2) .

This is reachable via the legacy zip extractor, which is wired into the generic `archive.Extractor` interface used by `CacheExtractorCommand.Execute` for cache extraction [4](#0-3) [5](#0-4) . Notably, other extractors in the same codebase (e.g., `tarzstd`) implement an explicit chroot check — `filepath.Abs(filepath.Join(e.dir, hdr.Name))` followed by a `strings.HasPrefix(path, e.dir+...)` guard that rejects escapes [6](#0-5)  — confirming that the legacy zip path lacks an equivalent, proven mitigation pattern.

An attacker who controls the contents of a cache or artifact zip (e.g. via a job's `cache:` or `artifacts:` configuration, or a poisoned cache pulled from a shared cache backend) can include entries named `..\..\..\CACHE_METADATA` (Windows) or `../../CACHE_METADATA` (any platform) to write outside the intended extraction directory, because there is no root-confinement check anywhere in this code path.

### Impact Explanation
If the extraction working directory happens to be inside or adjacent to a location where a `CACHE_METADATA` env-file or other env-file consumed later by `cache-archiver`/`cache-extractor` resides, a crafted archive entry can overwrite that file's contents before it is read by a subsequent command invocation, potentially injecting attacker-controlled cache keys/URLs or other data into the cache-archiving/extraction flow. More generally, this primitive allows writing arbitrary file content to arbitrary paths reachable by the runner process's file permissions, which can affect files used by later job stages (env-files, scripts, or state files) within the same job's execution context.

### Likelihood Explanation
Feasible and repeatable: any pipeline author can control the contents of an artifact/cache zip uploaded for their own job. Whether it is later fetched and extracted by `CacheExtractorCommand` (or artifacts) depends on normal cache/artifact workflow, which is standard, unprivileged CI functionality — no special runner configuration or privilege escalation is required to reach `ExtractZipArchive` with attacker-supplied `zip.File.Name` values.

### Recommendation
Add root-confinement validation to `helpers/archives/zip_extract.go`, mirroring the pattern already used in `tarzstd_extractor.go`: pass a destination root into `ExtractZipArchive`/`extractZipFile`, compute `filepath.Abs(filepath.Join(root, file.Name))` for every entry, and reject any entry whose resolved path is not prefixed by `root` (also reject absolute paths and drive-letter paths in `file.Name` explicitly, since Windows treats both `/` and `\` as separators). Apply the same fix to symlink targets to prevent symlink-based escapes.

### Proof of Concept
Go unit test outline (add to `helpers/archives/zip_extract_test.go`):
```go
func TestExtractZipFileZipSlip(t *testing.T) {
    testOnArchive(t, func(t *testing.T, archive *zip.Writer) {
        f, err := archive.Create("../evil.txt") // or "..\\..\\evil.txt" on windows
        require.NoError(t, err)
        _, err = io.WriteString(f, "pwned")
        require.NoError(t, err)
    }, func(t *testing.T, fileName string) {
        wd, _ := os.Getwd()
        parentPath := filepath.Join(filepath.Dir(wd), "evil.txt")
        defer os.Remove(parentPath)

        err := ExtractZipFile(fileName)
        require.NoError(t, err)

        _, statErr := os.Stat(parentPath)
        assert.NoError(t, statErr, "expected zip-slip file to escape extraction root")
    })
}
```
Expected (pre-fix): file is created outside intended directory, proving no confinement exists. Post-fix: `ExtractZipArchive` should return an error and no file should be written outside the root.

### Citations

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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L24-32)
```go
// Extract extracts files from the reader to the directory passed to
// NewZipExtractor.
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
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
