### Title
Zip Slip path traversal in `extractZipFileEntry`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry` allows writes outside extraction root - (File: helpers/archives/zip_extract.go)

### Summary
`ExtractZipArchive` iterates `zip.File` entries and passes `file.Name` verbatim to `os.MkdirAll`, `os.Mkdir`, `os.OpenFile`, `os.Remove`, and `os.Symlink` with no validation that the resulting path stays inside the current working (extraction) directory. A crafted zip entry name containing `../` sequences (or an absolute path) can therefore write, overwrite, or symlink files anywhere the runner/helper process has permission to write, including outside the job's working directory.

### Finding Description
`extractZipFile` (helpers/archives/zip_extract.go:61-83) computes the parent directory with `filepath.Dir(file.Name)` and calls `os.MkdirAll` on it unconditionally [1](#0-0) , then dispatches to `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, or `extractZipFileEntry` depending on file mode. Each of those functions uses `file.Name` directly as the destination path for `os.Mkdir` [2](#0-1) , `os.Symlink` [3](#0-2) , or `os.OpenFile`/`os.Remove` [4](#0-3) . None of these paths are cleaned, checked for absolute-path prefixes, or checked to ensure the resolved path remains a descendant of the extraction root.

The only existing content check is `errorIfGitDirectory`, which only rejects paths whose first cleaned component is literally `.git` [5](#0-4)  — it does nothing to stop `../` traversal or absolute paths. The Go standard library's `archive/zip` reader does not sanitize `File.Name` either; it is taken from the archive as-is.

`ExtractZipFile` opens the archive and calls `ExtractZipArchive` with no additional guard [6](#0-5) . Cache/artifact extraction flows (`CacheExtractorCommand.Execute` → `archive.NewExtractor` → registered zip extractor) ultimately reach this code for archives an attacker fully controls as job cache/artifact content [7](#0-6) . Since job cache upload content is attacker-controlled (their own job), a job can produce a cache zip with an entry named e.g. `../../../../home/user/.bashrc`, and when that cache is subsequently pulled and extracted, the write escapes the intended cache/build directory.

### Impact Explanation
An attacker (any job author using a cache with `pull` policy) can cause the extraction code to write or overwrite arbitrary files reachable by the runner/helper process outside the job's working directory — e.g. overwriting shell profile files, SSH `authorized_keys`, or other files the runner user can write, giving a path to persistence/lateral movement on the host running the job (subject to filesystem permissions of the process, so more severe on shell executor or when the helper runs with elevated write access).

### Likelihood Explanation
Feasible and repeatable: a normal user can generate an arbitrary zip file (bypassing normal `zip`/`archive/zip` tooling that would refuse `../` names, since raw archive writing APIs allow arbitrary `Name` fields), upload it as the project's job cache, and trigger extraction on the next job run using that cache key. No special privileges are required, and the vulnerability triggers deterministically on every extraction of a maliciously named entry, limited only by the OS write permissions of the process performing extraction.

### Recommendation
Before performing any filesystem operation in `extractZipFile`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry`, validate that `filepath.Clean(file.Name)` is relative (reject absolute paths) and that the resolved destination path (`filepath.Join(extractionRoot, file.Name)`) stays within the extraction root — reject or skip entries where `filepath.Clean` produces a path starting with `..` or where `filepath.Rel` from the root yields a path starting with `..`/is absolute. Apply the same check consistently to `errorIfGitDirectory`-style tracking so a single "actionable" path-escape error can be logged/suppressed like other `os.PathError`s.

### Proof of Concept
Go unit test (add to helpers/archives/zip_extract_test.go style):
```go
func TestExtractZipFilePathTraversalRejected(t *testing.T) {
    testOnArchive(t, func(t *testing.T, archive *zip.Writer) {
        f, err := archive.Create("../evil_escape.txt")
        require.NoError(t, err)
        _, err = io.WriteString(f, "pwned")
        require.NoError(t, err)
    }, func(t *testing.T, fileName string) {
        wd, _ := os.Getwd()
        parentTarget := filepath.Join(filepath.Dir(wd), "evil_escape.txt")
        defer os.Remove(parentTarget)

        err := ExtractZipFile(fileName)
        // Expect: extraction fails or the traversal entry is rejected
        assert.Error(t, err)
        _, statErr := os.Stat(parentTarget)
        assert.True(t, os.IsNotExist(statErr), "file must not be written outside extraction root")
    })
}
```
Current behavior: the test would fail today (no error, and `evil_escape.txt` is created outside the working directory), confirming the flaw; after adding path-containment validation, the test should pass.

### Citations

**File:** helpers/archives/zip_extract.go (L12-20)
```go
func extractZipDirectoryEntry(file *zip.File) (err error) {
	err = os.Mkdir(file.Name, file.Mode().Perm())

	// The "directory does exist" error is not an error for us
	if os.IsExist(err) {
		err = nil
	}
	return
}
```

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

**File:** helpers/archives/zip_extract.go (L61-66)
```go
func extractZipFile(file *zip.File) (err error) {
	// Create all parents to extract the file
	err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
	if err != nil {
		return err
	}
```

**File:** helpers/archives/zip_extract.go (L112-120)
```go
func ExtractZipFile(fileName string) error {
	archive, err := zip.OpenReader(fileName)
	if err != nil {
		return err
	}
	defer func() { _ = archive.Close() }()

	return ExtractZipArchive(&archive.Reader)
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

**File:** commands/helpers/cache_extractor.go (L655-663)
```go
	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
	}
```
