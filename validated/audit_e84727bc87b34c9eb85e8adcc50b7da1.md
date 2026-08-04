### Title
Zip-slip path traversal in ZIP extraction allows `os.Chtimes` (and Mkdir/OpenFile/Symlink) to touch paths outside the extraction root - (File: helpers/archives/zip_extract.go, helpers/archives/zip_extra.go)

### Summary
`extractZipFile` and `processZipTimestampField` both use `file.Name`/`file.FileHeader.Name` verbatim, with the only existing guard (`errorIfGitDirectory`) checking merely whether the first path segment is `.git`, never rejecting `..` traversal segments. A crafted zip entry with a `Name` like `../../somefile` and a regular-file/dir `Mode()` will cause `os.MkdirAll(filepath.Dir(file.Name), ...)` and later `os.Chtimes(file.Name, ...)` to operate outside the intended extraction directory.

### Finding Description
The extraction flow is:
`ExtractZipArchive` (helpers/archives/zip_extract.go:85-110) iterates `archive.File`, calling `errorIfGitDirectory(file.Name)` — which only rejects paths whose first cleaned segment equals `.git` (helpers/archives/path_check_helper.go:13-19) — and then `extractZipFile(file)`.

`extractZipFile` (zip_extract.go:61-83) does:
```go
err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
```
with `file.Name` completely unsanitized, then dispatches to `extractZipDirectoryEntry`/`extractZipSymlinkEntry`/`extractZipFileEntry`, all of which call `os.Mkdir`, `os.Symlink`, or `os.OpenFile` directly on `file.Name`. None of these clean or contain the path relative to a jail/extraction root.

After all entries are written, `ExtractZipArchive` does a second pass calling `processZipExtra(&file.FileHeader)` (zip_extract.go:104), which for a `ZipTimestampFieldType` extra field calls `processZipTimestampField` (zip_extra.go:50-68):
```go
if !file.Mode().IsDir() && !file.Mode().IsRegular() {
    return nil
}
...
return os.Chtimes(file.Name, acTime, modTime)
```
`file.Mode()` and `file.Name` are both taken straight from the attacker-supplied `zip.FileHeader`, with no re-validation that `file.Name` resolves inside the extraction root. Since the earlier `extractZipFile` step already created the file/directory at the attacker-chosen path via `MkdirAll`+`OpenFile`, `Chtimes` simply re-targets that same already-escaped path.

No component in this path performs `filepath.Clean` + prefix-containment checks against a base/root directory; `isPathAGitDirectory` only defends against `.git` overwrite, not directory traversal.

### Impact Explanation
An attacker who controls the contents of a zip archive that Runner extracts (cache archive contents, artifact archive contents when downloaded and locally extracted, or any use of `archives.ExtractZipArchive`/`ExtractZipFile`) can supply entries with names such as `../../../other-project/cache/file` or absolute-like relative traversal sequences. This lets the job:
- create/overwrite files (via `extractZipFileEntry`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry`) outside the job's designated extraction directory, and
- modify file **mtimes/atimes** of arbitrary paths reachable by the runner process's file permissions via `os.Chtimes`.

On shared-host setups (e.g., shell executor or shared cache/artifact storage where multiple projects' data live under a common parent on the same filesystem, not full container/chroot isolation), this can corrupt or disturb another project's cache/artifact files' content and timestamps, potentially invalidating cache correctness for other pipelines or clobbering unrelated files the runner process can write to.

### Likelihood Explanation
Feasible and repeatable: it only requires crafting a zip archive with `FileHeader.Name` fields containing `../` segments and an extra field of type `0x5455` (timestamp) — both are things a user fully controls when constructing a cache/artifact zip uploaded by their own job (cache is populated from job-defined paths and re-extracted later by Runner). The only check present (`.git` first-segment match) does not filter traversal sequences at all, so no additional bypass is needed. Precondition is that the runner/extraction root is not otherwise sandboxed (e.g., shell executor, or shared filesystem for artifacts/cache) — this matches the stated preconditions of the question.

### Recommendation
Add a path-containment check to `helpers/archives/path_check_helper.go` (or directly in `extractZipFile`/`ExtractZipArchive`) that:
1. `filepath.Clean`s `file.Name`,
2. joins it against the intended extraction root,
3. verifies the resulting absolute path has the extraction root as a strict prefix (using `filepath.Rel` or similar, rejecting results starting with `..` or being absolute),
before performing any `Mkdir`, `MkdirAll`, `OpenFile`, `Symlink`, `Lchmod`, or `Chtimes` operation. Reject/skip entries that fail this check the same way `.git` entries are currently handled, and apply the identical check inside `processZipExtra`/`processZipTimestampField`/`processZipUIDGidField` (or better, resolve/validate the safe path once and reuse it for all operations on that entry, rather than re-trusting `file.Name` in each helper independently).

### Proof of Concept
Go unit test to add to `helpers/archives/zip_extract_test.go`:
```go
func TestExtractZipFilePathTraversal(t *testing.T) {
    testOnArchive(t, func(t *testing.T, archive *zip.Writer) {
        f, err := archive.Create("../../evil_outside_root.txt")
        require.NoError(t, err)
        _, err = io.WriteString(f, "traversal payload")
        require.NoError(t, err)
    }, func(t *testing.T, fileName string) {
        // extract inside a temp jail dir
        jail := t.TempDir()
        wd, _ := os.Getwd()
        require.NoError(t, os.Chdir(jail))
        defer os.Chdir(wd)

        err := ExtractZipFile(fileName)
        require.NoError(t, err)

        // Assert the file was NOT created outside jail root (two levels up)
        escapedPath := filepath.Join(filepath.Dir(filepath.Dir(jail)), "evil_outside_root.txt")
        _, statErr := os.Stat(escapedPath)
        assert.True(t, os.IsNotExist(statErr), "zip-slip: file escaped extraction root")
    })
}
```
Expected result today: the assertion fails because `os.MkdirAll(filepath.Dir("../../evil_outside_root.txt"))` and subsequent `os.OpenFile`/`os.Chtimes` (if a timestamp extra field is added) succeed outside the jail, proving the traversal. After the fix, the entry should be rejected/skipped and the file must not exist outside the jail root. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** helpers/archives/zip_extract.go (L61-83)
```go
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

**File:** helpers/archives/zip_extra.go (L50-68)
```go
func processZipTimestampField(data []byte, file *zip.FileHeader) error {
	if !file.Mode().IsDir() && !file.Mode().IsRegular() {
		return nil
	}

	var tsField ZipTimestampField
	err := binary.Read(bytes.NewReader(data), binary.LittleEndian, &tsField)
	if err != nil {
		return err
	}

	if (tsField.Flags & 1) == 1 {
		modTime := time.Unix(int64(tsField.ModTime), 0)
		acTime := time.Now()
		return os.Chtimes(file.Name, acTime, modTime)
	}

	return nil
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
