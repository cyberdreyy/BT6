### Title
Zip-Slip path traversal in `ExtractZipArchive` lets attacker-controlled `os.Lchown` change ownership of files outside the extraction root - (File: `helpers/archives/zip_extract.go`, `helpers/archives/zip_extra_unix.go`)

### Summary
`ExtractZipArchive` in `helpers/archives/zip_extract.go` uses `file.Name` directly for `os.Mkdir`/`os.OpenFile`/`os.Symlink` and later for `processZipExtra` → `processZipUIDGidField` → `os.Lchown`, with no path-traversal validation anywhere in the extraction path. The only content check present, `errorIfGitDirectory`, only rejects `.git/...` prefixes, not `../` traversal or absolute paths. This means a crafted zip (cache/artifact) with a traversal `file.Name` and an Info-ZIP `0x7875` UID/GID extra field can both write outside the intended extraction directory and issue `os.Lchown(path, attackerUID, attackerGID)` against that out-of-root path.

### Finding Description
`ExtractZipArchive` [1](#0-0)  iterates `archive.File` and calls `extractZipFile(file)` which does `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and then, depending on entry type, `os.Mkdir`, `os.Symlink`, or `os.OpenFile`/`io.Copy`, all keyed on the raw `file.Name` [2](#0-1) . The only pre-write check is `errorIfGitDirectory`, which strips only `.git` path-prefix cases [3](#0-2) . There is no `filepath.Clean`/root-confinement check rejecting `../` segments or absolute paths, so a zip entry named e.g. `../../../../etc/cron.d/evil` (or an absolute path) is extracted wherever it resolves to on the filesystem (Zip-Slip).

After the write pass, a second loop calls `processZipExtra(&file.FileHeader)` for every entry [4](#0-3) , which dispatches on the `0x7875` extra-field type to `processZipUIDGidField` [5](#0-4) . That function parses attacker-supplied `UID`/`Gid` values straight out of the archive's extra-field bytes and calls `os.Lchown(file.Name, int(ugField.UID), int(ugField.Gid))` with no bounds or ownership check [6](#0-5) . Since `file.Name` is the same unsanitized, traversal-capable path used for the write step, any file the traversal reached can have its owner/group reassigned to attacker-chosen values.

Nothing in the pipeline (`ExtractZipFile`, `ziplegacy` extractor at `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`) performs root confinement before calling into `archives.ExtractZipArchive` [7](#0-6) , and the existing test suite only exercises benign filenames and the `.git`-warning path, not traversal [8](#0-7) , confirming no defense is exercised or present for this case.

### Impact Explanation
Where the runner (or its cache/artifact-extraction helper) executes with `CAP_CHOWN`/root privileges — a common deployment for shell executors and some helper-image configurations — an unprivileged pipeline author can craft a cache/artifact zip whose entries traverse outside the job workspace and carry a UID/GID extra field. On extraction this both (a) writes/overwrites arbitrary files outside the workspace and (b) re-chowns those files to an attacker-chosen UID/GID (e.g. root or a service account), directly violating the invariant that job-supplied archive metadata must not set runner-enforced identity boundaries. Even independent of the chown, the unmitigated Zip-Slip write itself is a serious file-write-outside-root primitive.

### Likelihood Explanation
Preconditions: attacker needs only the ability to control a cache/artifact zip's contents (trivial for any pipeline author using `cache:`/`artifacts:` or a custom archive uploaded and later extracted by another job/runner) and the runner extraction process needs write/chown privilege beyond the sandbox (true for shell executor as root, and for some helper-image setups). No authentication bypass or admin compromise is required — this is fully reachable from standard job-controlled archive content, and reliably reproducible since `ExtractZipArchive` performs no traversal validation whatsoever.

### Recommendation
1. In `extractZipFile` (and before any file-system operation in `ExtractZipArchive`), canonicalize `file.Name` against the extraction root (e.g. `filepath.Clean`, reject entries with `..` elements or absolute paths, and verify the resolved path via `filepath.Rel`/prefix check stays under the destination root) before `Mkdir`/`OpenFile`/`Symlink`.
2. Apply the same root-confinement check in `processZipExtra`/`processZipUIDGidField` before calling `os.Lchown`, or simply skip UID/GID and mode/timestamp extra-field processing for any entry whose path failed the traversal check.
3. Consider dropping unprivileged-uid/gid honoring entirely for job-triggered extraction (cache/artifacts), since ownership metadata from a job-controlled archive should never be trusted to set arbitrary UID/GID.

### Proof of Concept
Go unit test to add to `helpers/archives/zip_extract_test.go`:
```go
func TestExtractZipFileZipSlipTraversal(t *testing.T) {
    testInWorkDir(t, func(t *testing.T, fileName string) {
        f, err := os.Create(fileName)
        require.NoError(t, err)
        zw := zip.NewWriter(f)

        // crafted entry escaping the extraction root
        w, err := zw.Create("../outside_root_evil.txt")
        require.NoError(t, err)
        _, err = io.WriteString(w, "pwned")
        require.NoError(t, err)
        require.NoError(t, zw.Close())
        f.Close()

        err = ExtractZipFile(fileName)
        require.NoError(t, err)

        // Assert the file was NOT created outside the extraction root
        _, statErr := os.Stat(filepath.Join(filepath.Dir(fileName), "..", "outside_root_evil.txt"))
        assert.True(t, os.IsNotExist(statErr), "Zip-Slip entry escaped the extraction root")
    })
}
```
A companion fuzz/unit test should craft a `ZipUIDGidField` extra field (type `0x7875`) attached to a traversal-named entry, run as a privileged test process (e.g. under `CAP_CHOWN` or root in CI), and assert that the resulting file's owner (`syscall.Stat_t.Uid/Gid`) is **not** equal to the attacker-supplied UID/GID, and that no file outside the temp extraction directory was created or had its ownership changed.

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

**File:** helpers/archives/zip_extra.go (L96-119)
```go
func processZipExtra(file *zip.FileHeader) error {
	if len(file.Extra) == 0 {
		return nil
	}

	r := bytes.NewReader(file.Extra)
	for {
		field, data, err := readZipExtraField(r)
		if err == io.EOF {
			break
		} else if err != nil {
			return err
		}

		switch field.Type {
		case ZipUIDGidFieldType:
			err = processZipUIDGidField(data, file)
		case ZipTimestampFieldType:
			err = processZipTimestampField(data, file)
		}
		if err != nil {
			return err
		}
	}
```

**File:** helpers/archives/zip_extra_unix.go (L37-48)
```go
func processZipUIDGidField(data []byte, file *zip.FileHeader) error {
	var ugField ZipUIDGidField
	err := binary.Read(bytes.NewReader(data), binary.LittleEndian, &ugField)
	if err != nil {
		return err
	}

	if !(ugField.Version == 1 && ugField.UIDSize == 4 && ugField.GIDSize == 4) {
		return errors.New("uid/gid data not supported")
	}

	return os.Lchown(file.Name, int(ugField.UID), int(ugField.Gid))
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

**File:** helpers/archives/zip_extract_test.go (L17-92)
```go
func createDefaultArchive(t *testing.T, archive *zip.Writer) {
	testFile, err := archive.Create("temporary_file.txt")
	require.NoError(t, err)
	_, err = io.WriteString(testFile, "test file")
	require.NoError(t, err)
}

func createArchiveWithGitPath(t *testing.T, archive *zip.Writer) {
	testGitFile, err := archive.Create(".git/test_file")
	require.NoError(t, err)
	_, err = io.WriteString(testGitFile, "test git file")
	require.NoError(t, err)
}

func testOnArchive(
	t *testing.T,
	createArchive func(t *testing.T, archive *zip.Writer),
	testCase func(t *testing.T, fileName string),
) {
	tempFile, err := os.CreateTemp("", "archive")
	require.NoError(t, err)
	defer tempFile.Close()
	defer os.Remove(tempFile.Name())

	archive := zip.NewWriter(tempFile)
	defer archive.Close()

	createArchive(t, archive)
	archive.Close()
	tempFile.Close()

	testCase(t, tempFile.Name())
}

func TestExtractZipFile(t *testing.T) {
	testOnArchive(t, createDefaultArchive, func(t *testing.T, fileName string) {
		err := ExtractZipFile(fileName)
		require.NoError(t, err)

		stat, err := os.Stat("temporary_file.txt")
		assert.False(t, os.IsNotExist(err), "Expected temporary_file.txt to exist")
		if !os.IsNotExist(err) {
			assert.NoError(t, err)
		}

		if stat != nil {
			defer os.Remove("temporary_file.txt")
			assert.Equal(t, int64(9), stat.Size())
		}
	})
}

func TestExtractZipFileWithGitPath(t *testing.T) {
	testOnArchive(t, createArchiveWithGitPath, func(t *testing.T, fileName string) {
		output := logrus.StandardLogger().Out
		var buf bytes.Buffer
		logrus.SetOutput(&buf)
		defer logrus.SetOutput(output)

		err := ExtractZipFile(fileName)
		require.NoError(t, err)

		assert.Contains(t, buf.String(), "Part of .git directory is on the list of files to extract")

		stat, err := os.Stat(".git/test_file")
		assert.False(t, os.IsNotExist(err), "Expected .git/test_file to exist")
		if !os.IsNotExist(err) {
			assert.NoError(t, err)
		}

		if stat != nil {
			defer os.Remove(".git/test_file")
			assert.Equal(t, int64(13), stat.Size())
		}
	})
}
```
