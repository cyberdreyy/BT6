### Title
`extractZipFile` writes to absolute/traversal paths from zip archive entries without root confinement - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipFile` (and the directory/file/symlink helpers it calls) use `file.Name` from the zip archive directly as a filesystem path for `os.MkdirAll`, `os.OpenFile`, `os.Mkdir`, and `os.Symlink`, with no check that the name is relative or confined to the intended extraction root. The only sanitization present is `errorIfGitDirectory`, which only detects a leading `.git` path component and has nothing to do with path traversal or absolute paths.

### Finding Description
`ExtractZipArchive` iterates `archive.File` entries and calls `extractZipFile(file)` for each one [1](#0-0) . Inside `extractZipFile`, the code does:

```go
err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
```
and then dispatches to `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, or `extractZipFileEntry`, all of which call `os.Mkdir(file.Name, ...)`, `os.Symlink(data, file.Name)`, or `os.OpenFile(file.Name, ...)` directly using the attacker-controlled `file.Name` [2](#0-1) .

There is no call to reject or clean absolute paths (`filepath.IsAbs`), no rejection of `..` traversal segments, and no confinement of the resolved path to a base/root directory. The only pre-extraction check is `errorIfGitDirectory`, which merely splits the cleaned path and checks whether the first component is `.git` — it does nothing to prevent absolute paths, drive-qualified Windows paths (e.g. `C:\...`), UNC paths, or `../../` traversal sequences, and even when it does detect such an entry it only logs a warning (`printGitArchiveWarning`) rather than blocking extraction [3](#0-2) , [4](#0-3) .

This extraction path is reachable from cache/artifact restore via `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`, whose `Extract` method opens the zip reader and calls `archives.ExtractZipArchive(zr)` directly — notably, the `extractor.dir` field captured at construction is never referenced inside `Extract`, so there is no `os.Chdir`/root confinement performed by this extractor implementation itself [5](#0-4) . If entry names are absolute (e.g. `/etc/foo`, `C:\Windows\System32\...`) or contain `..` segments that resolve outside the working directory, `filepath.Dir(file.Name)` and the subsequent `os.OpenFile`/`os.Symlink` calls will operate on that absolute/escaped path as-is, because Go's `os` file APIs do not sandbox absolute paths.

### Impact Explanation
An attacker who controls the contents of a cache or artifact zip archive consumed by a job (their own job's cache/artifact, or a shared/predictable cache key) can craft entries with absolute or traversal path names. When restored via this extraction code, files can be written or symlinked outside the intended build/cache directory, potentially overwriting files elsewhere on the executor filesystem the runner process has permission to write to. This matches the scoped impact of cross-job state tampering or overwrite of files outside the assigned extraction root.

### Likelihood Explanation
This requires only the ability to produce/upload a cache or artifact archive consumed later by extraction — a capability an ordinary pipeline author already has (defining `cache`/`artifacts` and controlling job output). No admin privileges, no compromise of GitLab, and no unusual environment access are needed. The bug is deterministic and repeatable: any archive with an absolute or `..`-containing entry name reaching `ExtractZipArchive` triggers it.

### Recommendation
Before dispatching each `zip.File` to extraction, validate and normalize `file.Name`: reject entries where `filepath.IsAbs(file.Name)` is true (covering POSIX absolute and Windows drive/UNC forms), and reject/resolve entries whose cleaned relative path, when joined to the extraction root, escapes that root (the standard zip-slip guard: `target := filepath.Join(root, file.Name); if !strings.HasPrefix(target, root+string(filepath.Separator)) { reject }`). Apply this check uniformly in `extractZipFile` (and the analogous tar/legacy extractors) rather than relying solely on the `.git`-specific check.

### Proof of Concept
Go test in `helpers/archives`:
```go
func TestExtractZipFile_RejectsAbsolutePath(t *testing.T) {
    testOnArchive(t, func(t *testing.T, archive *zip.Writer) {
        f, err := archive.Create("/etc/passwd_evil") // or "../../evil" for traversal
        require.NoError(t, err)
        _, _ = io.WriteString(f, "pwned")
    }, func(t *testing.T, fileName string) {
        err := ExtractZipFile(fileName)
        // Expect an error/rejection instead of a written file outside cwd
        require.Error(t, err)
        _, statErr := os.Stat("/etc/passwd_evil")
        assert.True(t, os.IsNotExist(statErr), "file must not be created outside extraction root")
    })
}
```
Currently this test would fail (no error, and depending on permissions the file gets created at the absolute path), demonstrating the missing confinement check.

### Citations

**File:** helpers/archives/zip_extract.go (L12-66)
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

func extractZipFile(file *zip.File) (err error) {
	// Create all parents to extract the file
	err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
	if err != nil {
		return err
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
