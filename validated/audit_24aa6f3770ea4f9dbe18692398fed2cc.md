### Title
Zip extraction follows attacker-controlled symlinks, allowing writes outside extraction root - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipSymlinkEntry` writes a symlink target directly from attacker-controlled archive data with no validation that the target stays within the extraction root, and `extractZipFile`/`extractZipFileEntry` perform no check that a subsequent entry's resolved path (after following any symlinked parent directory) remains inside that root. This allows a two-entry cache/artifact zip (a symlink entry followed by a nested file entry) to write a file to an arbitrary location on the host, such as `/etc` or elsewhere outside the intended build/cache directory.

### Finding Description
`ExtractZipArchive` iterates `archive.File` in order and calls `extractZipFile` for each entry [1](#0-0) . For an entry with `os.ModeSymlink`, `extractZipSymlinkEntry` reads the link target straight from the zip entry's data and calls `os.Symlink(string(data), file.Name)` with no check that the resolved target stays inside the extraction directory [2](#0-1) . For subsequent entries, `extractZipFile` does `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and then dispatches to `extractZipFileEntry`, which calls `os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, ...)` using the raw `file.Name` from the archive with no `filepath.Clean`/root-containment check and no `O_NOFOLLOW`-style symlink protection [3](#0-2) . The only pre-write validation present is `errorIfGitDirectory`, which only blocks paths under `.git` and has nothing to do with symlink escapes [4](#0-3) .

Given an attacker-crafted zip with entries ordered `[symlink "link" -> "/tmp", regular file "link/pwned"]`:
1. `extractZipFile` processes the symlink entry, creating a real filesystem symlink named `link` pointing to `/tmp` (or any absolute/relative-escaping path) inside the extraction directory.
2. `extractZipFile` processes the next entry `"link/pwned"`; `filepath.Dir("link/pwned")` is `"link"`, which the OS resolves through the just-created symlink to `/tmp`. `os.MkdirAll` and then `os.OpenFile` write `pwned` through the symlink into `/tmp/pwned`, entirely outside the intended extraction root.

This is reachable via the legacy zip extraction path used for artifacts/cache: `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`'s `Extract` calls `archives.ExtractZipArchive(zr)` directly on a zip built from user/job-controlled cache or artifact content [5](#0-4) , and `ExtractZipFile` (used by `ExtractZipArchive` callers generally) has the same lack of validation [6](#0-5) . Existing tests only cover git-directory warnings and symlink mode preservation, not path/symlink escape, confirming no test currently guards this invariant [7](#0-6) .

Cache/artifact zip contents are attacker-controlled by the job author (a job can produce arbitrary artifact/cache paths and content that end up being downloaded and extracted by a Runner, e.g., on a later job or the same job's cache restore), satisfying the "unprivileged job input" precondition.

### Impact Explanation
An unprivileged pipeline author can craft a cache or artifact archive that, upon extraction by Runner, escapes the intended cache/build directory and writes attacker-controlled file content to an arbitrary path reachable by the Runner process's filesystem permissions (e.g., overwriting files under `/tmp`, or any directory the Runner process can write to, depending on executor/filesystem layout). This is an out-of-root arbitrary file write via cache/artifact poisoning, matching the scoped impact.

### Likelihood Explanation
The precondition is simply the ability to control the contents of a cache or artifact zip — something any pipeline author can do via normal job scripts (e.g., `ln -s /tmp/target link && touch link/pwned` before caching/uploading the directory, or by direct zip construction). The exploit requires only two crafted zip entries in the right order and no special privileges, so it is straightforward and repeatable across any Runner deployment using the ziplegacy extraction path.

### Recommendation
In `extractZipSymlinkEntry`, validate that `filepath.Clean(filepath.Join(baseDir, string(data)))` (for relative targets) or the target itself (for absolute targets) resolves within the extraction root before calling `os.Symlink`; reject or drop symlink entries whose target escapes. Additionally, in `extractZipFile`/`extractZipFileEntry`/`extractZipDirectoryEntry`, resolve `file.Name`'s parent directories (e.g., via `filepath.EvalSymlinks` on the existing portion of the path, or by tracking created symlinks and refusing to traverse through them) and reject any write whose resolved absolute path is not still contained within the extraction root, mirroring the containment checks used in hardened tar/zip extraction (`archive/zip` slip protections, similar to Go's `os/exec`-safe extraction patterns or `filepath.IsLocal`).

### Proof of Concept
Go test in `helpers/archives/zip_extract_test.go`:
```go
func TestExtractZipSymlinkEscape(t *testing.T) {
    testInWorkDir(t, func(t *testing.T, fileName string) {
        outsideDir := t.TempDir() // simulate /tmp or other outside root
        f, err := os.Create(fileName)
        require.NoError(t, err)
        archive := zip.NewWriter(f)

        // symlink entry pointing outside the extraction root
        linkHeader := &zip.FileHeader{Name: "link"}
        linkHeader.SetMode(os.ModeSymlink | 0o777)
        linkWriter, err := archive.CreateHeader(linkHeader)
        require.NoError(t, err)
        _, err = linkWriter.Write([]byte(outsideDir))
        require.NoError(t, err)

        // nested file entry that should be written "through" the symlink
        fileWriter, err := archive.Create("link/pwned")
        require.NoError(t, err)
        _, err = fileWriter.Write([]byte("attacker-controlled"))
        require.NoError(t, err)

        require.NoError(t, archive.Close())
        require.NoError(t, f.Close())

        err = ExtractZipFile(fileName)
        require.NoError(t, err)

        // Assert the file was NOT created outside the extraction root
        _, statErr := os.Stat(filepath.Join(outsideDir, "pwned"))
        assert.True(t, os.IsNotExist(statErr), "expected 'pwned' to not exist outside extraction root, escape occurred")
    })
}
```
Expected current behavior (bug present): `os.Stat(filepath.Join(outsideDir, "pwned"))` succeeds, proving the file was written outside the extraction root. After the fix, this stat should return `os.IsNotExist`.

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

**File:** helpers/archives/zip_extract.go (L41-66)
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

**File:** helpers/archives/zip_extract_test.go (L94-137)
```go
func TestExtractZipFileNotFound(t *testing.T) {
	err := ExtractZipFile("non_existing_zip_file.zip")
	assert.Error(t, err)
}

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
}
```
