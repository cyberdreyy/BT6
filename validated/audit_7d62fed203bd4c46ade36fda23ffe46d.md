### Title
Zip Slip / path traversal in extractZipFile allows writing files outside the extraction root - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipFile` and its helpers (`extractZipDirectoryEntry`, `extractZipSymlinkEntry`, `extractZipFileEntry`) use `file.Name` directly from the zip entry — via `os.MkdirAll(filepath.Dir(file.Name), 0o777)`, `os.OpenFile(file.Name, ...)`, `os.Mkdir(file.Name, ...)`, and `os.Symlink(..., file.Name)` — with no `filepath.Join` against a root directory and no rejection of absolute paths or `..` traversal segments. The only path check performed, `errorIfGitDirectory`, only detects a leading `.git` component and does nothing to prevent path escape.

### Finding Description
`ExtractZipArchive` ( [1](#0-0) ) iterates `archive.File` and calls `extractZipFile(file)` for every entry, passing `file.Name` unmodified. Inside `extractZipFile`: [2](#0-1) 

`os.MkdirAll(filepath.Dir(file.Name), 0o777)` is called on the raw, attacker-supplied `file.Name`. Depending on entry type, `extractZipDirectoryEntry` calls `os.Mkdir(file.Name, ...)`, `extractZipSymlinkEntry` calls `os.Remove(file.Name)` / `os.Symlink(data, file.Name)`, and `extractZipFileEntry` calls `os.Remove(file.Name)` / `os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, ...)` — all directly on `file.Name` ( [3](#0-2) ). None of these paths are joined against an extraction root or checked for `..`/absolute-path escape. The only path-related guard, `errorIfGitDirectory`, checks only whether the cleaned path's first component is `.git` and only prints a warning without blocking extraction ( [4](#0-3) ) — it does not prevent traversal or absolute paths.

The reachable call path from an unprivileged job is: `CacheExtractorCommand.Execute` opens the archive and constructs an extractor with the working directory `wd`, then calls `extractor.Extract` ( [5](#0-4) ). The zip extractor implementation, however, ignores the `dir` field entirely — `Extract` just calls `archives.ExtractZipArchive(zr)` without ever using `e.dir` ( [6](#0-5) ). So extraction happens directly against entry names relative to (or, for absolute paths, ignoring) the process CWD, with no root confinement whatsoever.

Because a cache/artifact zip archive's contents (including entry names) are attacker-controlled — an unprivileged pipeline author can produce a cache archive with crafted entries such as `../../../etc/foo`, or an absolute path `/tmp/evil`, or nested `..` sequences — the extraction will write, overwrite, or symlink files at attacker-chosen filesystem locations relative to (or independent of) the runner's working directory when that cache/artifact is later restored.

### Impact Explanation
An unprivileged job can craft a cache/artifact archive whose entries escape the intended cache directory, causing the runner helper process to write, truncate, or symlink arbitrary files reachable by the executing user outside the job's cache/build root — e.g., overwriting sibling jobs' cache files, other files in a shared home/cache directory, or files elsewhere on disk that the runner process user can write to. This directly violates the invariant that "job-controlled paths must not cause host file access outside job root."

### Likelihood Explanation
This is easily reproducible: any pipeline author can supply a cache key/archive whose zip contents they fully control (cache push/pull is a normal, unprivileged pipeline feature), then have another job (or the same job on a later run) pull that cache, triggering `CacheExtractorCommand.Execute` -> `ExtractZipArchive` -> `extractZipFile`. No special executor privileges or admin misconfiguration are required — only that the runner extracts a zip cache/artifact, which is default behavior.

### Recommendation
Sanitize every `file.Name` before any filesystem operation in `extractZipFile`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry`/`extractZipFileEntry`: reject absolute paths, clean the name, and use `filepath.Join(rootDir, cleanedName)` then verify (e.g. via `filepath.Rel` or a prefix check) that the resulting path stays within `rootDir` before calling `os.MkdirAll`, `os.Mkdir`, `os.OpenFile`, `os.Remove`, or `os.Symlink`. Also fix `ziplegacy.extractor.Extract` (and `ExtractZipArchive`) to actually take and enforce an extraction-root directory (`e.dir`) rather than ignoring it.

### Proof of Concept
Go unit test in `helpers/archives`:
```go
func TestExtractZipFile_PathTraversal(t *testing.T) {
    tmp := t.TempDir()
    cwd, _ := os.Getwd()
    require.NoError(t, os.Chdir(tmp))
    defer os.Chdir(cwd)

    outside := filepath.Join(filepath.Dir(tmp), "escaped.txt")
    defer os.Remove(outside)

    // build in-memory zip with entry "../escaped.txt"
    buf := &bytes.Buffer{}
    zw := zip.NewWriter(buf)
    w, _ := zw.Create("../escaped.txt")
    w.Write([]byte("pwned"))
    zw.Close()

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)
    err = ExtractZipArchive(zr)
    require.NoError(t, err)

    _, statErr := os.Stat(outside)
    assert.Error(t, statErr, "file must not be written outside the extraction root")
}
```
Expected (buggy) result: `escaped.txt` is created outside `tmp`, proving the path escape; after the fix, the assertion should pass because the write is rejected/confined.

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

**File:** helpers/archives/zip_extract.go (L61-66)
```go
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
