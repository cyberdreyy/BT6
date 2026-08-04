### Title
Zip-slip via unvalidated symlink targets in legacy zip archiver/extractor - ([File: helpers/archives/zip_create.go], [File: helpers/archives/zip_extract.go])

### Summary
`createZipSymlinkEntry` stores the raw, unvalidated symlink target returned by `os.Readlink` into the zip entry, and the corresponding `extractZipSymlinkEntry`/`extractZipFile` on the extraction side recreate that symlink and blindly `MkdirAll` into any path component without ever checking that the resolved path stays inside the extraction root. This is the classic "zip slip via symlink" pattern and it is not mitigated in this code path, unlike the newer `tarzstd` extractor which does an explicit chroot check.

### Finding Description
`createZipEntry` in `helpers/archives/zip_create.go` dispatches symlinked files to `createZipSymlinkEntry`, which does: [1](#0-0) 
It stores whatever `os.Readlink(fh.Name)` returns — an attacker-controlled value since the attacker created the symlink inside `$CI_PROJECT_DIR` — with no check that the target is relative/confined.

On extraction, `extractZipFile` in `helpers/archives/zip_extract.go` first does `os.MkdirAll(filepath.Dir(file.Name), 0o777)` for every entry, then for symlink entries calls `extractZipSymlinkEntry`, which removes any existing file at `file.Name` and calls `os.Symlink(string(data), file.Name)` with the raw stored target and no validation: [2](#0-1) [3](#0-2) 

Neither the writer nor the reader verifies that a symlink stays within the archive root. Two exploitation angles exist:
1. Direct symlink recreation: if a job creates a symlink e.g. `link -> /etc/passwd` (or a writable/target path shared across build dirs) and includes it via `artifacts:paths`/cache `paths`, the resulting zip stores `/etc/passwd` as the raw link target. When the archive is later extracted (by the same job, a downstream job, or any consumer that unzips the artifact), `os.Symlink("/etc/passwd", "link")` recreates that dangling/absolute symlink inside the new job's workspace.
2. Zip-slip write-through: if the archive additionally contains a nested entry such as `link/evil.txt`, `extractZipFile`'s `os.MkdirAll(filepath.Dir("link/evil.txt"))` will resolve into the symlink target's directory (because `MkdirAll` follows existing symlinks) and `extractZipFileEntry` will then write `evil.txt` through the followed symlink, landing outside the intended extraction root (e.g., into another project's build directory or any directory writable by the runner process).

No path-confinement checks exist in this file, unlike the sibling extractor `tarzstd_extractor.go`, which explicitly computes `filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects any resolved path that escapes `e.dir`: [4](#0-3) 
The legacy zip extractor (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`) simply delegates to `archives.ExtractZipArchive`, discarding the `dir` confinement parameter entirely and inheriting the unguarded behavior: [5](#0-4) 

The existing `errorIfGitDirectory`/`pathErrorTracker` checks only guard against `.git` directory extraction warnings, not against symlink escape: [6](#0-5) 

### Impact Explanation
On a shared runner (especially shell executor, or any executor sharing a filesystem across jobs/projects with the runner's user), an unprivileged pipeline author can craft an artifact/cache archive whose extraction writes files outside the intended build/cache/artifact root — into another project's build directory or any path writable by the runner process. This violates the "file operations must stay within intended build/cache/artifact roots" invariant and enables cross-project artifact/cache poisoning on the extracting side.

### Likelihood Explanation
Preconditions are minimal and fully attacker-controlled: create a symlink in `$CI_PROJECT_DIR` pointing to an absolute or relative-escaping path, include it (plus an optional nested file entry) via `artifacts:paths`/cache `paths`, and trigger the normal archive-then-extract round trip that GitLab CI already performs for every job with artifacts/cache. No admin privileges or special GitLab configuration are required, only that the runner uses (or falls back to) the legacy zip format path (`ziplegacy`), which has no confinement logic at all.

### Recommendation
Add root-confinement validation in `helpers/archives/zip_extract.go` mirroring `tarzstd_extractor.go`: before creating any file/dir/symlink, resolve `filepath.Join(rootDir, file.Name)` with `filepath.Abs` and reject entries whose resolved path escapes `rootDir`. Additionally, validate symlink targets in `extractZipSymlinkEntry` (reject absolute targets and targets that resolve outside the root after joining with the link's directory), and pass/use the `dir` parameter threaded through `ziplegacy.NewExtractor` instead of discarding it. Apply the equivalent write-side check in `createZipSymlinkEntry` to avoid archiving symlinks whose targets point outside the intended source root.

### Proof of Concept
Go unit test added to `helpers/archives/zip_extract_test.go`:
```go
func TestExtractZipFileSymlinkEscape(t *testing.T) {
    testInWorkDir(t, func(t *testing.T, fileName string) {
        outsideDir := t.TempDir() // simulate directory outside extraction root
        f, err := os.Create(fileName)
        require.NoError(t, err)
        defer f.Close()

        archive := zip.NewWriter(f)
        // symlink entry pointing to an absolute path outside the workspace
        fh := &zip.FileHeader{Name: "escape_link"}
        fh.SetMode(os.ModeSymlink | 0o777)
        w, err := archive.CreateHeader(fh)
        require.NoError(t, err)
        _, err = io.WriteString(w, outsideDir)
        require.NoError(t, err)

        // nested file entry that would be written "through" the symlink
        fw, err := archive.Create("escape_link/pwned.txt")
        require.NoError(t, err)
        _, err = io.WriteString(fw, "pwned")
        require.NoError(t, err)
        require.NoError(t, archive.Close())
        f.Close()

        err = ExtractZipFile(fileName)
        require.NoError(t, err)

        // Assert extraction fails closed: pwned.txt must NOT exist outside the workspace
        _, statErr := os.Stat(filepath.Join(outsideDir, "pwned.txt"))
        assert.True(t, os.IsNotExist(statErr), "file must not be written outside extraction root via symlink")
    })
}
```
Expected current behavior (bug present): `pwned.txt` is created inside `outsideDir`, proving the escape. Expected fixed behavior: `ExtractZipFile` returns an error or skips the entry, and `pwned.txt` is never created outside the workspace root.

### Citations

**File:** helpers/archives/zip_create.go (L17-30)
```go
func createZipSymlinkEntry(archive *zip.Writer, fh *zip.FileHeader) error {
	fw, err := archive.CreateHeader(fh)
	if err != nil {
		return err
	}

	link, err := os.Readlink(fh.Name)
	if err != nil {
		return err
	}

	_, err = io.WriteString(fw, link)
	return err
}
```

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
