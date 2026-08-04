### Title
Zip-slip path traversal in `ExtractZipArchive`/`extractZipFile` allows writing outside extraction root - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipFile` and its helpers (`extractZipFileEntry`, `extractZipSymlinkEntry`, `extractZipDirectoryEntry`) use `zip.File.Name` verbatim to build filesystem paths with no traversal sanitization, unlike the sibling tar+zstd extractor which explicitly rejects paths outside its target directory. A job-controlled cache/artifact zip can therefore place arbitrary file/symlink content anywhere the runner process has write access, including outside the job workspace (e.g. a user's PowerShell profile), and `lchmod` on Windows performs no path validation either, so nothing in the call chain blocks the escape.

### Finding Description
`ExtractZipArchive` iterates `archive.File` and, for each entry, calls `extractZipFile(file)` [1](#0-0) , which does:
```
err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
```
followed by `extractZipFileEntry`/`extractZipSymlinkEntry`/`extractZipDirectoryEntry`, all of which call `os.Mkdir`, `os.OpenFile`, or `os.Symlink` directly on `file.Name` [2](#0-1) . There is no `filepath.Clean`, no `filepath.Abs`/`filepath.Rel` check, and no comparison against an extraction root anywhere in this file. The only path validation present, `errorIfGitDirectory`/`isPathAGitDirectory`, only detects a literal `.git` leading path segment and is unrelated to traversal defense [3](#0-2) .

Compare this to the tar+zstd extractor in the same codebase, which does enforce containment:
```
path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))
...
if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir {
    return fmt.Errorf("%s cannot be extracted outside of chroot (%s)", path, e.dir)
}
``` [4](#0-3) 
No equivalent check exists for zip extraction. Worse, the legacy zip extractor wrapper receives a target `dir` but never uses it when calling into `archives.ExtractZipArchive`:
```
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	...
	return archives.ExtractZipArchive(zr)
}
``` [5](#0-4) 
so extraction paths are resolved purely relative to the process's current working directory, which is whatever `wd` the cache extractor command set via `os.Getwd()` before invoking `extractor.Extract` [6](#0-5) . A crafted `zip.File.Name` such as `../../../Users/<user>/Documents/WindowsPowerShell/profile.ps1` will cause `os.MkdirAll(filepath.Dir(...))` to create the parent directories outside the workspace, and `extractZipFileEntry` will then write attacker-controlled content into `profile.ps1`.

After all files are written, `ExtractZipArchive` calls `lchmod(file.Name, file.Mode())` for every entry [7](#0-6) . On Windows this simply does `os.Chmod` on `name` (skipping symlinks) [8](#0-7) ; it performs no path re-validation, so it neither introduces nor prevents the traversal — it just executes against whatever traversed path was already written.

### Impact Explanation
An unprivileged pipeline author who controls a cache or artifact zip consumed via `cache_extractor`/`ziplegacy` extraction can write or overwrite arbitrary files on the runner host filesystem outside the job workspace, limited only by the OS-level permissions of the runner process user. On a Windows runner this can target auto-loaded PowerShell profile scripts (or other auto-executed files), leading to code execution in a subsequent shell/PowerShell session beyond the authored job payload — a runner-host compromise via cache/artifact zip-slip.

### Likelihood Explanation
The only precondition is that a job can supply/control a cache or artifact archive that is later extracted with `archives.ExtractZipArchive` on a Windows executor (a normal, unprivileged capability of any GitLab CI job that uses caching/artifacts). No admin action, misconfiguration, or peer compromise is required, and the code path is directly reachable through the documented cache-extraction flow (`CacheExtractorCommand.Execute` → `archive.NewExtractor` → `ziplegacy.extractor.Extract` → `archives.ExtractZipArchive`).

### Recommendation
Sanitize and validate each `zip.File.Name` against an explicit extraction root before any filesystem operation, mirroring the approach used in `tarzstd_extractor.go`: join the entry name with the target directory, resolve to an absolute path, and reject (or log-and-skip) any entry whose resolved path is not prefixed by the extraction root. Apply this check once in `ExtractZipArchive`/`extractZipFile` before `os.MkdirAll`, and thread the actual extraction root through `ziplegacy.extractor.Extract` instead of relying on the process CWD.

### Proof of Concept
```go
func TestExtractZipArchive_RejectsPathTraversal(t *testing.T) {
    tmpDir := t.TempDir()
    zipPath := filepath.Join(tmpDir, "evil.zip")

    f, err := os.Create(zipPath)
    require.NoError(t, err)
    zw := zip.NewWriter(f)
    w, err := zw.Create("../outside_workspace.txt")
    require.NoError(t, err)
    _, err = w.Write([]byte("pwned"))
    require.NoError(t, err)
    require.NoError(t, zw.Close())
    require.NoError(t, f.Close())

    workDir := filepath.Join(tmpDir, "workspace")
    require.NoError(t, os.MkdirAll(workDir, 0777))

    // simulate cwd-based extraction as done by ExtractZipFile
    origWD, _ := os.Getwd()
    defer os.Chdir(origWD)
    require.NoError(t, os.Chdir(workDir))

    err = archives.ExtractZipFile(zipPath)
    require.NoError(t, err)

    escapedPath := filepath.Join(tmpDir, "outside_workspace.txt")
    _, statErr := os.Stat(escapedPath)
    // Expected (after fix): file should NOT exist outside workDir
    assert.True(t, os.IsNotExist(statErr), "zip entry escaped extraction root: %s", escapedPath)
}
```
Currently this test fails (the file is created outside `workDir`), demonstrating the zip-slip; after adding root-containment validation it should pass.

### Citations

**File:** helpers/archives/zip_extract.go (L12-66)
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

**File:** helpers/archives/zip_extract.go (L98-101)
```go
	for _, file := range archive.File {
		if err := lchmod(file.Name, file.Mode()); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L26-32)
```go
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
```

**File:** commands/helpers/cache_extractor.go (L626-660)
```go
	wd, err := os.Getwd()
	if err != nil {
		logrus.Fatalln("Unable to get working directory")
	}

	if c.File == "" {
		warningln("Missing cache file")
	}

	if c.URL != "" || c.GoCloudURL != "" {
		err := c.doRetry(c.download)
		if err != nil {
			warningln(err)
		}
	} else {
		logrus.Infoln(
			"No URL provided, cache will not be downloaded from shared cache server. " +
				"Instead a local version of cache will be extracted.")
	}

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
```

**File:** helpers/archives/os_windows.go (L9-13)
```go
func lchmod(name string, mode os.FileMode) error {
	if mode&os.ModeSymlink != 0 {
		return nil
	}
	return os.Chmod(name, mode.Perm())
```
