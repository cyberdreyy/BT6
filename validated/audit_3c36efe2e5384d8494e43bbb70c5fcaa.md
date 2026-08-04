## Analysis: Confirmed Vulnerability



I found a concrete, reachable bug matching this threat model.

### Title
CreateZipArchive/ExtractZipArchive embed OS-native path separators verbatim, letting cross-platform archives escape the extraction root (Zip Slip via backslash-as-separator confusion) - (File: helpers/archives/zip_create.go, helpers/archives/zip_extract.go)

### Summary
`CreateZipArchive` stores `fh.Name = fileName` verbatim [1](#0-0) , and the caller's normalization via `filepath.ToSlash` is a no-op on Linux/macOS since only the OS's own separator is converted [2](#0-1) . A file whose name legally contains literal backslashes and `..` segments (valid on Unix filesystems/git, since only `/` and NUL are forbidden in a path component) is archived with that literal string as the zip entry name. On extraction, `extractZipFile` performs zero path-traversal validation — no `filepath.Rel`/prefix check against the destination root exists anywhere in `zip_extract.go`, unlike the tar/zstd extractor which explicitly chroots the path [3](#0-2) . If that archive is later extracted on a Windows runner/executor, `filepath.Dir`/`os.MkdirAll` on Windows treat backslash as a real separator, turning the previously-opaque filename into an actual `..`-traversal path.

### Finding Description
The exploit chain:
1. **Attacker input**: An unprivileged pipeline author commits a file (or creates one at runtime) whose name is a single path component containing literal backslashes and `..`, e.g. `artifacts/..\..\..\Windows\Temp\evil.txt`. This is a completely valid filename on Linux — backslash is not a path separator there.
2. **Archiving**: `fileArchiver.process`/`add` computes the relative path and only normalizes with `filepath.ToSlash`, which is a no-op on Unix (only converts `os.PathSeparator`, not arbitrary backslash characters) [4](#0-3) . The `..`-prefix guard in `findRelativePathInProject`/`process` only checks real path-separator-delimited components via `filepath.Rel`/`HasPrefix`, so it cannot detect `..` sequences embedded inside a single opaque filename component [5](#0-4) .
3. `CreateZipArchive` writes `fh.Name` as-is into the zip header with no separator canonicalization or rejection of embedded `..`/backslash sequences [6](#0-5) .
4. **Restore/extraction**: `ExtractZipArchive`/`extractZipFile` extracts each `file.Name` directly with `os.MkdirAll(filepath.Dir(file.Name), ...)` and `os.OpenFile`/`os.Symlink` — there is no root-containment check anywhere in the function [7](#0-6) . Compare this to the tar/zstd extractor, which explicitly computes `filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects paths outside the chroot [3](#0-2)  — that protection is absent for zip.
5. If the artifact/cache archive created on a Linux runner is later restored on a Windows runner (a normal cross-platform GitLab Runner fleet scenario — cache/artifacts are shared across jobs/pipelines potentially running on different executors/OSes), the Windows Go runtime's `filepath` functions treat `\` as a real separator, and `..` components that were inert on Linux become a genuine directory traversal that can write outside the intended extraction directory.

### Impact Explanation
This allows a cross-job/cross-pipeline path-root escape on Windows-hosted restore: extracted artifact/cache content can be written outside the designated build/cache directory, potentially overwriting files belonging to other jobs or, depending on the account running the extraction, arbitrary writable locations on the host filesystem. This matches the "cross-job overwrite or path-root escape after restore" impact class.

### Likelihood Explanation
- Precondition: a heterogeneous runner fleet (some Linux, some Windows) sharing the same cache/artifact storage, or any workflow where an archive produced on Unix is consumed by a Windows-based extraction path.
- Attacker only needs commit/MR access to add a maliciously-named file and a `.gitlab-ci.yml` `artifacts:paths`/`cache:paths` entry (or default artifact paths) that includes it — fully within an unprivileged pipeline author's control.
- No special timing or race is required; the bug is deterministic and repeatable every time the archive crosses OS extraction context.

### Recommendation
- In `CreateZipArchive`/`createZipEntry`, canonicalize `fh.Name` to forward-slash zip convention (`filepath.ToSlash` is insufficient on Unix; explicitly reject or escape literal backslash characters in path components) before writing the header.
- In `ExtractZipArchive`/`extractZipFile`, add the same containment check used in the tar/zstd extractor: resolve `filepath.Abs(filepath.Join(destRoot, file.Name))` and reject entries whose resolved path is not a descendant of `destRoot`, regardless of the separator style embedded in the stored name.

### Proof of Concept
Go test sketch:
```go
func TestZipCreate_EmbeddedBackslashTraversal(t *testing.T) {
    testInWorkDir(t, func(t *testing.T, fileName string) {
        // Simulate a Unix filename containing literal backslashes + ".." — legal on Unix.
        evilName := `dir` + string(os.PathSeparator) + `..\..\..\evil.txt`
        require.NoError(t, os.MkdirAll("dir", 0755))
        require.NoError(t, os.WriteFile(evilName, []byte("pwned"), 0644))

        f, err := os.Create(fileName)
        require.NoError(t, err)
        require.NoError(t, CreateZipArchive(f, []string{evilName}))
        require.NoError(t, f.Close())

        archive, err := zip.OpenReader(fileName)
        require.NoError(t, err)
        defer archive.Close()

        // Assert the stored entry name still contains the literal backslash sequence unmodified,
        // proving no cross-platform-safe canonicalization occurred.
        assert.Contains(t, archive.File[0].Name, `..\`)
    })
}
```
A companion assertion on the extractor side would show `extractZipFile` has no guard rejecting `filepath.Dir`/`filepath.Join` results that fall outside the extraction root when run with a Windows-style separator interpretation, confirming the missing containment check identified in `zip_extract.go`.

### Citations

**File:** helpers/archives/zip_create.go (L52-66)
```go
func createZipEntry(archive *zip.Writer, fileName string) error {
	fi, err := os.Lstat(fileName)
	if err != nil {
		logrus.Warningln("File ignored:", err)
		return nil
	}

	fh, err := zip.FileInfoHeader(fi)
	if err != nil {
		return err
	}
	fh.Name = fileName
	fh.Extra = createZipExtra(fi)
	// Set EFS flag to indicate that filenames and comments are UTF-8 encoded
	fh.Flags |= 0x800
```

**File:** commands/helpers/file_archiver.go (L69-87)
```go
	absolute, err = filepath.Abs(match)
	if err == nil {
		// Let's try to find a real relative path to an absolute from working directory
		relative, err = filepath.Rel(c.wd, absolute)
	}

	if err == nil {
		// Process path only if it lives in our build directory
		if !strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			excluded, rule := c.isExcluded(relative)
			if excluded {
				c.exclude(rule)
				return false
			}

			err = c.add(relative)
		} else {
			err = errors.New("not supported: outside build directory")
		}
```

**File:** commands/helpers/file_archiver.go (L126-134)
```go

func (c *fileArchiver) add(path string) error {
	// Always use slashes
	path = filepath.ToSlash(path)

	// Check if file exist
	info, err := os.Lstat(path)
	if err == nil {
		c.files[path] = info
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
