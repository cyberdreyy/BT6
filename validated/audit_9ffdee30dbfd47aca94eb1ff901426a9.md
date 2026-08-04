### Title
Zip extraction in `extractZipFile` allows path traversal + symlink write, escaping the extraction root - (helpers/archives/zip_extract.go)

### Summary
`extractZipFile` performs no validation that `file.Name` stays within the intended extraction directory before calling `os.MkdirAll(filepath.Dir(file.Name), 0o777)`, and `extractZipSymlinkEntry` then calls `os.Symlink` with an attacker-controlled link target and an attacker-controlled `file.Name`. Since `file.Name` and the symlink payload both come directly from a zip archive an unprivileged job can control (cache/legacy zip artifact archives extracted via `ziplegacy.extractor` → `archives.ExtractZipArchive`), a crafted entry with `..` traversal segments in `Name` and `os.ModeSymlink` set can create directories and plant a symlink outside the job's working directory.

### Finding Description
`extractZipFile` at `helpers/archives/zip_extract.go:61-83` does: `os.MkdirAll(filepath.Dir(file.Name), 0o777)` unconditionally, then switches on `file.Mode() & os.ModeType`. For `os.ModeSymlink` it calls `extractZipSymlinkEntry`, which reads the symlink target from the zip entry's *contents* and calls `os.Symlink(string(data), file.Name)` (`helpers/archives/zip_extract.go:22-39`) — with no check that `file.Name` is confined to the extraction root, and no check on the target string either.

The only sanitization present in this path is `errorIfGitDirectory` (`helpers/archives/path_check_helper.go:13-31`), which only blocks paths whose first component is literally `.git`; it does not block `../` traversal or absolute paths, and it is only a warning-and-continue (`tracker.actionable`), not a hard stop.

Compare this to the tar-zstd extractor (`commands/helpers/archive/tarzstd/tarzstd_extractor.go:57-64`), which explicitly computes `filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects any path that doesn't have `e.dir` as a prefix ("cannot be extracted outside of chroot"). The zip path (`helpers/archives/zip_extract.go`, wired through `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`) has no equivalent chroot check — `file.Name` is used as-is, relative to the process's current working directory, with no join/verify against the extraction `dir` argument at all.

Reachability: `ziplegacy.extractor.Extract` (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go:26-32`) calls `zip.NewReader` then directly `archives.ExtractZipArchive(zr)`, which is registered as a `Format` extractor via `archive.Register`/`archive.NewExtractor` (`commands/helpers/archive/archive.go`) and invoked from `commands/helpers/cache_extractor.go:655-660` on the job-provided cache archive content. A job (or an attacker who can influence uploaded cache/artifact zip content processed by this code path) fully controls every `zip.File.Name`, `Mode`, and content byte in that archive.

Given that, the described chain is real: a `zip.File` entry with `Name = "../../evil/marker"` and `Mode()&os.ModeType == os.ModeSymlink` will cause:
1. `os.MkdirAll(filepath.Dir("../../evil/marker"), 0o777)` → creates `../../evil` relative to CWD, i.e., outside the intended extraction directory.
2. `extractZipSymlinkEntry` → `os.Symlink(<attacker link target>, "../../evil/marker")` → plants a symlink outside the extraction root pointing anywhere the process has permission to reach (e.g., another checked-out project directory or a file containing `CI_JOB_TOKEN`, subject to OS filesystem permissions of the runner process/executor).

### Impact Explanation
This breaks the "file operations must stay within intended build/cache/artifact roots" invariant. Depending on executor and filesystem layout (e.g., shell executor with shared builds directory, or any executor where the extraction directory and other project checkouts/token files are on the same filesystem visible to the job's OS user), an unprivileged pipeline author can use a malicious cache/legacy-zip archive to create directories and symlinks outside the job workspace, potentially reading or corrupting files from another project's checkout or a token/secret file that a subsequent job step reads through the symlink. The actual reach is bounded by OS file permissions of the executing user, but no Runner-level control (path validation) stops it — the underlying `os.MkdirAll`/`os.Symlink` calls will succeed as long as the OS permits them.

### Likelihood Explanation
Feasible and repeatable: it only requires crafting a standard Go `archive/zip` file with a `File.Name` containing `../` and mode bits/`FileHeader.SetMode` set to a symlink, with the compressed body set to the desired link target string. No special privileges are needed beyond controlling the content of a zip processed by `ExtractZipArchive`/`ziplegacy` (cache archives, or any consumer using the legacy zip path). This is a deterministic, always-reproducible logic bug, not a race condition.

### Recommendation
In `extractZipFile` (and ideally centrally in `ExtractZipArchive`), resolve each `file.Name` against the known extraction root with `filepath.Join(root, file.Name)` followed by `filepath.Abs` and a `strings.HasPrefix(resolved, root+string(filepath.Separator))` check (mirroring the tarzstd extractor's chroot check) before calling `os.MkdirAll` or any file/symlink creation. Additionally validate/reject symlink targets that resolve outside the extraction root (not just the entry name), since `extractZipSymlinkEntry`'s link data is equally attacker-controlled.

### Proof of Concept
```go
func TestExtractZipFile_PathTraversalSymlink(t *testing.T) {
    root := t.TempDir()
    outsideMarker := filepath.Join(filepath.Dir(root), "outside-target")
    require.NoError(t, os.WriteFile(outsideMarker, []byte("secret"), 0o644))

    zipPath := filepath.Join(root, "evil.zip")
    f, _ := os.Create(zipPath)
    zw := zip.NewWriter(f)
    hdr := &zip.FileHeader{Name: "../escaped-symlink"}
    hdr.SetMode(os.ModeSymlink | 0o777)
    w, _ := zw.CreateHeader(hdr)
    _, _ = w.Write([]byte(outsideMarker))
    zw.Close()
    f.Close()

    wd, _ := os.Getwd()
    defer os.Chdir(wd)
    os.Chdir(root)

    err := archives.ExtractZipFile(zipPath)
    require.NoError(t, err)

    linkPath := filepath.Join(filepath.Dir(root), "escaped-symlink")
    resolved, err := filepath.EvalSymlinks(linkPath)
    require.NoError(t, err)

    // Assert the resolved symlink stayed within root; this FAILS today,
    // proving the escape.
    assert.True(t, strings.HasPrefix(resolved, root+string(filepath.Separator)),
        "symlink escaped extraction root: resolved to %s", resolved)
}
```
Expected today: the assertion fails, demonstrating that `extractZipFile`/`extractZipSymlinkEntry` create a symlink outside the extraction root. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L57-64)
```go
		var path string
		path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))
		if err != nil {
			return err
		}
		if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir {
			return fmt.Errorf("%s cannot be extracted outside of chroot (%s)", path, e.dir)
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
