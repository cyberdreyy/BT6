### Title
Zip-Slip Path Traversal in ExtractZipArchive Allows Writing Outside Job Workspace - (File: `helpers/archives/zip_extract.go`)

### Summary
`ExtractZipArchive` and its helpers (`extractZipFile`, `extractZipFileEntry`, `extractZipDirectoryEntry`, `extractZipSymlinkEntry`) use `zip.File.Name` directly to build filesystem paths (`os.MkdirAll(filepath.Dir(file.Name))`, `os.OpenFile(file.Name, ...)`, `os.Symlink`) with no validation against path traversal or absolute paths. The only safety check performed (`errorIfGitDirectory`) rejects `.git` paths, not `..` traversal or absolute/UNC paths.

### Finding Description
`extractZipFile` at [1](#0-0)  calls `os.MkdirAll(filepath.Dir(file.Name), 0o777)` directly on the attacker-supplied `zip.File.Name`, and `extractZipFileEntry` at [2](#0-1)  opens `file.Name` for writing with no cleaning, `filepath.Clean`, or containment check against the intended extraction root. `extractZipSymlinkEntry` at [3](#0-2)  is similarly unguarded and lets an attacker create a symlink at an arbitrary relative path pointing anywhere, which could be leveraged for a two-step traversal-and-follow write.

`ExtractZipArchive` at [4](#0-3)  only screens each entry through `errorIfGitDirectory`, which exclusively checks whether the first path component is `.git`; it performs no check for `..` segments or absolute/drive-letter Windows paths. The `path_check_helper.go` logic at [5](#0-4)  confirms this — it is purpose-built for git-directory detection, not traversal prevention.

Critically, `extractor.Extract` in `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go` stores a `dir` field intended as the extraction root but never passes or uses it: `archives.ExtractZipArchive(zr)` at [6](#0-5)  is called with no root confinement argument at all, relying entirely on the current working directory and on `zip.File.Name` being "safe," which it is not validated to be.

Exploit flow: a pipeline author controls the contents of a cache or artifact archive uploaded from a prior job stage (job scripts fully control what gets zipped and its filenames, since these are ordinary files packed by GitLab Runner's own archiver, but a crafted/uploaded artifact/cache blob or a maliciously constructed zip pushed as a dependency artifact between jobs can contain manipulated `zip.FileHeader.Name` entries such as `..\..\..\somefile` or `C:\Users\...\build.ps1`). When a later job (potentially on a different runner/build directory but same working tree) restores this cache/artifact via `ExtractZipArchive`, the traversal segments are honored verbatim by `os.MkdirAll`/`os.OpenFile`, writing outside the job's designated build directory — e.g., overwriting the generated build script before the shell executor runs it, or clobbering files elsewhere on the runner host filesystem within the permissions of the runner process.

### Impact Explanation
An attacker who can influence the contents of a cache/artifact zip (a normal, unprivileged capability of any pipeline author) can escape the job workspace root and write attacker-controlled file content to arbitrary paths reachable by the runner process's user, including potentially overwriting the shell executor's generated pre-build/build/post-build scripts, persisting malicious content across jobs/pipelines sharing the same shell-executor host, or corrupting files outside the sandboxed workspace — a direct violation of the "file operations must stay within intended build/cache/artifact roots" invariant.

### Likelihood Explanation
This is straightforward and fully attacker-controlled: any pipeline author can produce a cache/artifact archive with crafted entry names (standard zip-slip technique), and cache/artifact restoration through `ExtractZipArchive` is a routine, non-privileged operation performed automatically by Runner on every job that declares `cache`/`dependencies`. No special runner configuration or admin cooperation is required, only that the job (or an upstream job in the same project/pipeline) can supply the archive contents restored later.

### Recommendation
Before performing any filesystem operation in `extractZipFile`/`extractZipFileEntry`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry`, resolve the target path against the intended extraction root, reject absolute paths and any path whose cleaned form escapes the root (`!strings.HasPrefix(filepath.Clean(target), root+string(filepath.Separator))`), and reject `..` components outright (matching the standard zip-slip fix used in `archive/zip` extraction utilities). Ensure `ExtractZipArchive`/`ExtractZipFile` and the `ziplegacy` extractor actually chdir/confine extraction to the passed `dir` and validate every `file.Name` against it prior to `MkdirAll`/`OpenFile`/`Symlink`.

### Proof of Concept
Go unit test (added to `helpers/archives/zip_extract_test.go`):
```go
func TestExtractZipArchive_PathTraversal(t *testing.T) {
    tmpDir := t.TempDir()
    workDir := filepath.Join(tmpDir, "workspace")
    require.NoError(t, os.MkdirAll(workDir, 0o755))

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    f, err := zw.Create("../../evil.sh")
    require.NoError(t, err)
    _, err = f.Write([]byte("malicious"))
    require.NoError(t, err)
    require.NoError(t, zw.Close())

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)

    oldWd, _ := os.Getwd()
    defer os.Chdir(oldWd)
    require.NoError(t, os.Chdir(workDir))

    err = ExtractZipArchive(zr)
    require.NoError(t, err)

    escapedPath := filepath.Join(tmpDir, "evil.sh")
    _, statErr := os.Stat(escapedPath)
    assert.NoError(t, statErr, "expected traversal file to be written outside workDir, proving zip-slip")
}
```
Expected assertion: the file lands at `tmpDir/evil.sh` (outside `workDir`), demonstrating that `ExtractZipArchive` does not confine extracted entries to the intended root.

### Citations

**File:** helpers/archives/zip_extract.go (L22-39)
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
```

**File:** helpers/archives/zip_extract.go (L41-55)
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
