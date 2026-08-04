### Title
Zip-slip via unsanitized `zip.File.Name` allows path traversal in `extractZipFileEntry` - (File: `helpers/archives/zip_extract.go`)

### Summary
`extractZipFileEntry` (and its siblings `extractZipDirectoryEntry`/`extractZipSymlinkEntry`) call `os.OpenFile`, `os.Mkdir`, and `os.Symlink` directly on `file.Name` from an untrusted zip archive without validating that the resolved path stays within the extraction root. The only existing content check, `errorIfGitDirectory`, only blocks paths starting with `.git`, and does nothing to stop `../` traversal segments.

### Finding Description
`ExtractZipArchive` iterates `archive.File` and calls `extractZipFile(file)` for every entry [1](#0-0) . `extractZipFile` creates parent directories with `os.MkdirAll(filepath.Dir(file.Name), ...)` and then dispatches to `extractZipFileEntry`, which does `os.Remove(file.Name)` followed by `os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, ...)` [2](#0-1) . There is no call to `filepath.Clean`, no check for `..` segments, and no verification that the resulting absolute path is a descendant of the extraction directory. The only path-content check performed is `errorIfGitDirectory`, which merely detects a leading `.git` segment and only prints a warning (it doesn't abort extraction) [3](#0-2) . Nothing in `ExtractZipArchive` rejects `..`-containing names before calling `extractZipFile` [4](#0-3) .

`ExtractZipArchive` is reachable from `ExtractZipFile` (used by cache/artifact zip extraction paths) and from `commands/helpers/archive/ziplegacy` extractor, which is the legacy zip extraction backend used for cache/artifact archives [5](#0-4) . A malicious `zip.File.Name` such as `../../other-job-workspace/secret-file` would therefore be written literally relative to the process's current working directory (the extraction root, which the runner sets to the target build/cache directory before invoking extraction), allowing writes outside the intended directory.

### Impact Explanation
If an attacker can supply an artifact/cache zip archive whose entries contain relative paths with `../` segments, extraction will write/overwrite files outside the intended `CI_PROJECT_DIR` or cache extraction root. On a host with concurrent job execution sharing a filesystem (e.g., shell executor, or any executor where multiple jobs' extraction roots share a common parent visible to the runner process), this could allow one job's cache/artifact restore to overwrite files belonging to another job's workspace — a cross-job/cross-project file corruption or state-alteration primitive, matching the scoped "cross-project file overwrite/state alteration" impact.

### Likelihood Explanation
The archive content (file names inside the zip, including `../` sequences) is fully attacker-controlled by anyone who can produce the cache/artifact zip consumed later by `ExtractZipFile`/`ExtractZipArchive` (e.g., a pipeline author crafting a job that produces a cache or artifact archive with such entries, which is later restored by another job or the same job on a shared runner). No credentials or special privileges are needed beyond controlling job output that becomes a cache/artifact archive. This is straightforwardly reproducible with a hand-crafted zip file and a direct unit test against `extractZipFileEntry`/`ExtractZipArchive`, since no path-sanitization guard currently blocks it.

### Recommendation
Add a path-safety check in `extractZipFile` (or centrally in `ExtractZipArchive`) that resolves each entry's target path against the extraction root using `filepath.Clean`/`filepath.Rel` (or a helper similar to `os.Root`/zip-slip guards), and rejects (or skips with a warning, consistent with existing error handling via `pathErrorTracker`) any entry whose cleaned relative path starts with `../` or is absolute, before calling `os.Mkdir`, `os.Symlink`, or `os.OpenFile`. This mirrors the pattern already used for `errorIfGitDirectory`, but must actually abort extraction of the offending entry rather than only warn.

### Proof of Concept
```go
func TestExtractZipFileEntryPathTraversal(t *testing.T) {
	// Set up a temp extraction root and chdir into it, simulating CI_PROJECT_DIR
	root := t.TempDir()
	restore := changeToDir(t, root) // helper to os.Chdir(root) and restore afterwards
	defer restore()

	// Craft a malicious zip with a path-traversal entry
	buf := &bytes.Buffer{}
	zw := zip.NewWriter(buf)
	w, err := zw.Create("../../outside_secret.txt")
	require.NoError(t, err)
	_, err = w.Write([]byte("overwritten"))
	require.NoError(t, err)
	require.NoError(t, zw.Close())

	zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
	require.NoError(t, err)

	err = ExtractZipArchive(zr)
	require.NoError(t, err)

	// Assert the file landed OUTSIDE root, proving traversal succeeded
	outsidePath := filepath.Join(filepath.Dir(filepath.Dir(root)), "outside_secret.txt")
	_, statErr := os.Stat(outsidePath)
	assert.NoError(t, statErr, "expected zip-slip write to escape extraction root")
}
```
Expected result on the current code: the file is created outside `root`, confirming the traversal; after applying the recommended path-containment check, `ExtractZipArchive`/`extractZipFile` should reject the entry (e.g., return/track an error) and no file should be created outside `root`.

### Citations

**File:** helpers/archives/zip_extract.go (L41-59)
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L26-32)
```go
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
```
