### Title
Zip extraction and `lchmod` follow attacker-controlled `zip.File.Name` without path-traversal validation, allowing writes/chmod outside the extraction root - ([File: helpers/archives/zip_extract.go], [File: helpers/archives/os_unix.go])

### Summary
`extractZipFile` and the trailing `lchmod` loop in `ExtractZipArchive` use `file.Name` verbatim from the zip entry to create directories, files, symlinks, and to `chmod` via `unix.Fchmodat(unix.AT_FDCWD, name, ...)`, with no check that the resolved path stays within the intended extraction directory. The only path check present, `errorIfGitDirectory`, only blocks paths whose first component is `.git` and does nothing to prevent `../` traversal or absolute paths.

### Finding Description
`ExtractZipArchive` iterates `archive.File` and for each entry calls `extractZipFile(file)` [1](#0-0) , which does `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and then creates the file/dir/symlink using `file.Name` directly, with no `filepath.Clean`, no check for `..` segments, and no verification that the result is a descendant of the target directory [2](#0-1) . The individual entry handlers (`extractZipDirectoryEntry`, `extractZipSymlinkEntry`, `extractZipFileEntry`) also consume `file.Name` unchanged when calling `os.Mkdir`, `os.Symlink`, and `os.OpenFile` [3](#0-2) .

After all files are written, `ExtractZipArchive` runs a second loop calling `lchmod(file.Name, file.Mode())` for every entry [4](#0-3) . `lchmod` resolves `name` relative to `unix.AT_FDCWD` (i.e., the process current working directory) via `unix.Fchmodat(unix.AT_FDCWD, name, uint32(mode.Perm()), flags)` [5](#0-4) . Since `name` is the raw, unsanitized `file.Name`, a traversal string like `../../etc/passwd` or an absolute path resolves outside the extraction root and both the earlier write (via `extractZipFileEntry`) and the later `lchmod` operate on that external path.

The only existing guard, `errorIfGitDirectory`, splits the cleaned path and checks only whether the first component equals `.git` [6](#0-5)  — it does not detect or reject `..` traversal or absolute paths, so it provides no protection against this issue.

Reachability: `ExtractZipArchive` is invoked by `ziplegacy.extractor.Extract`, which is registered as the zip extractor and is reachable from cache/artifact extraction commands that operate on job-supplied cache/artifact zip files [7](#0-6) . Notably, the `extractor` struct stores a `dir` field intended to scope extraction to a target directory [8](#0-7) , but `Extract` never uses `e.dir` to change directory, chroot, or prefix paths before calling `archives.ExtractZipArchive(zr)` [7](#0-6) ; extraction paths are resolved purely against the process's current working directory with no confinement.

### Impact Explanation
An attacker who controls the contents of a cache or artifact zip (e.g., produced by an earlier job in the same pipeline, or a malicious artifact fetched and extracted by the runner) can name a zip entry `../../../etc/passwd` (or similar) to have `extractZipFileEntry` overwrite/create arbitrary files outside the job's working directory, and then have `lchmod` change the permission bits of that same out-of-scope file, since both operations use the unsanitized, traversal-capable `file.Name` resolved against `AT_FDCWD`/CWD. This breaks the invariant that archive operations stay confined to the build/cache/artifact root, resulting in host file write/overwrite and unauthorized permission changes outside the workspace.

### Likelihood Explanation
Feasible and repeatable with normal, unprivileged pipeline configuration: any job that can control an artifact or cache zip consumed later by the runner (a very ordinary CI capability) can embed traversal path entries. No special runner privileges, admin misconfiguration, or peer compromise is required — only the ability to shape zip entry names, which standard `archive/zip` writers make trivial. The vulnerability is purely a missing path-validation check in code that runs on every extraction.

### Recommendation
Before performing any operation with `file.Name` (Mkdir, MkdirAll, OpenFile, Symlink, and the `lchmod` call), resolve the target path against the extraction root, `filepath.Clean` it, and reject/skip entries whose resolved path is not a descendant of that root (reject absolute paths and paths containing `..` that escape the root). Thread the extraction root (`dir`) from `ziplegacy.extractor` into `archives.ExtractZipArchive`/`extractZipFile`/`lchmod` instead of relying on `AT_FDCWD`/implicit CWD, and add symlink-target validation to prevent symlink-based escapes as well.

### Proof of Concept
Go unit test in `helpers/archives`:
```go
func TestExtractZipArchive_RejectsPathTraversal(t *testing.T) {
    tmp := t.TempDir()
    outside := filepath.Join(tmp, "outside.txt")
    // pre-create the "outside" target to detect chmod tampering
    require.NoError(t, os.WriteFile(outside, []byte("orig"), 0o600))

    root := filepath.Join(tmp, "workspace")
    require.NoError(t, os.MkdirAll(root, 0o755))

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    for _, name := range []string{"../outside.txt", "../../etc/passwd"} {
        w, _ := zw.Create(name)
        _, _ = w.Write([]byte("pwned"))
    }
    require.NoError(t, zw.Close())

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)

    oldWd, _ := os.Getwd()
    defer os.Chdir(oldWd)
    require.NoError(t, os.Chdir(root))

    err = ExtractZipArchive(zr)
    require.NoError(t, err) // current code swallows errors via tracker/logging

    content, _ := os.ReadFile(outside)
    // Expected (fixed) behavior: file content must remain "orig" (write rejected)
    assert.Equal(t, "orig", string(content), "extraction escaped workspace root and overwrote outside file")
}
```
Expected assertion on the current (unfixed) code: `content` becomes `"pwned"`, proving the write escaped the workspace; a corresponding `lchmod` call on `../outside.txt` further modifies permissions on that same out-of-root file, confirming both the write and the `lchmod` traversal.

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

**File:** helpers/archives/zip_extract.go (L88-96)
```go
	for _, file := range archive.File {
		if err := errorIfGitDirectory(file.Name); tracker.actionable(err) {
			printGitArchiveWarning("extract")
		}

		if err := extractZipFile(file); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}
	}
```

**File:** helpers/archives/zip_extract.go (L98-107)
```go
	for _, file := range archive.File {
		if err := lchmod(file.Name, file.Mode()); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}

		// Process zip metadata
		if err := processZipExtra(&file.FileHeader); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}
	}
```

**File:** helpers/archives/os_unix.go (L12-28)
```go
func lchmod(name string, mode os.FileMode) error {
	var flags int

	if runtime.GOOS == "linux" {
		// Linux does not support changing modes on symlinks.
		if mode&os.ModeSymlink != 0 {
			return nil
		}
	} else {
		flags = unix.AT_SYMLINK_NOFOLLOW
	}

	err := unix.Fchmodat(unix.AT_FDCWD, name, uint32(mode.Perm()), flags)
	if err != nil {
		return &os.PathError{Op: "lchmod", Path: name, Err: err}
	}
	return nil
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L12-22)
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
