Confirmed: no absolute-path or path-traversal validation exists anywhere in `helpers/archives/zip_extract.go`. The only path-related check is `errorIfGitDirectory`, which only rejects `.git`-prefixed entries and never rejects absolute paths or `..` sequences. `extractZipFile` calls `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and then `extractZipFileEntry`/`extractZipSymlinkEntry`/`extractZipDirectoryEntry` operate directly on `file.Name` with `os.OpenFile`, `os.Symlink`, `os.Mkdir` — all of which honor absolute paths verbatim on Go/Unix/Windows. There's no `filepath.Join(destDir, file.Name)` or `filepath.IsAbs` check in this file.

### Title
Zip extraction writes to OS-absolute paths without validation - (File: helpers/archives/zip_extract.go)

### Summary
`ExtractZipArchive` (and the file-level helpers it calls) use `zip.File.Name` verbatim for all filesystem operations without ever checking whether the name is an OS-absolute path. Since `extractZipFile` calls `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and then `os.OpenFile`/`os.Symlink`/`os.Mkdir` directly on `file.Name`, a zip entry with an absolute name (e.g. `/etc/cron.d/evil`) writes straight to that host path, completely bypassing the intended extraction root.

### Finding Description
The extraction pipeline is: `ExtractZipFile` → `ExtractZipArchive` → `extractZipFile` → `extractZipFileEntry`/`extractZipSymlinkEntry`/`extractZipDirectoryEntry` [1](#0-0) [2](#0-1) 

The only content validation performed per entry is `errorIfGitDirectory`, which merely checks whether the first path segment is `.git` and is purely advisory (logs a warning, does not abort extraction): [3](#0-2) [4](#0-3) 

Neither `extractZipFile` nor its callers ever call `filepath.IsAbs(file.Name)`, join the name against a destination root, or use `filepath.Rel`/containment checks. `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and the subsequent `os.OpenFile`/`os.Symlink`/`os.Mkdir` calls operate on `file.Name` as given by the archive, so if `file.Name` is `/etc/cron.d/evil` (or a Windows drive-qualified path), Go's `filepath.Dir`/`os.*` APIs will resolve and create/write at that literal absolute location on the host filesystem, regardless of the working directory the caller intended as the extraction root (e.g. artifact/cache extraction root passed by `ArtifactsDownloaderCommand.Execute` or `CacheExtractorCommand.Execute`, both of which pass `wd` — the job's working directory — as the destination, expecting confinement).

The `commands/helpers/archive` package's `NewExtractor`/`Extract` wrapper (in `commands/helpers/archive/*`) sets up an extractor bound to a directory `wd`, but based on the code inspected, the underlying `archives.ExtractZipArchive` function has no dependency on or validation against that destination directory when acting on absolute entry names — it operates process-globally via `os.*` calls using the entry name directly.

### Impact Explanation
A malicious artifact/cache zip (attacker-controlled: a pipeline author can shape build artifacts or cache archives that get downloaded and extracted by `ArtifactsDownloaderCommand`/`CacheExtractorCommand`) containing a single entry with an absolute `Name` can cause the Runner process to create directories and write/overwrite arbitrary files at that absolute path with the permissions of the Runner process (e.g., planting a cron job, overwriting an authorized_keys file, or corrupting system files), entirely outside of the job's designated workspace/cache root. This breaks the "file operations must stay within intended build/cache/artifact roots" invariant without needing any `..` traversal.

### Likelihood Explanation
Feasibility is high: any pipeline author controls the contents of artifacts they upload, and cache archive contents can also be influenced by job scripts before caching. Crafting a zip with an absolute-path entry name is trivial (zip format permits arbitrary Name strings; Go's `archive/zip` reader does not reject absolute names on parse). No traversal encoding tricks or special privileges are needed — a single-entry archive suffices. This is deterministically repeatable.

### Recommendation
In `extractZipFile` (or earlier, in `ExtractZipArchive`), reject any `file.Name` that is `filepath.IsAbs(...)` or that, after `filepath.Clean`, escapes the destination root (also guard against `..` segments and Windows-style absolute/UNC paths). Ideally, extraction should take an explicit destination directory parameter and validate `filepath.Join(destDir, cleanedName)` stays under `destDir` (e.g. via `strings.HasPrefix` on cleaned absolute paths or using `filepath.Rel` and checking for a leading `..`) before performing any `os.MkdirAll`/`os.OpenFile`/`os.Symlink`/`os.Mkdir` call, erroring out (not just warning) when validation fails.

### Proof of Concept
```go
func TestExtractZipArchive_RejectsAbsolutePath(t *testing.T) {
    buf := new(bytes.Buffer)
    zw := zip.NewWriter(buf)
    // absolute path outside any workspace
    absTarget := filepath.Join(os.TempDir(), "gl-runner-poc-cron-evil") // simulate e.g. "/etc/cron.d/evil"
    fh := &zip.FileHeader{Name: absTarget}
    fh.SetMode(0644)
    w, err := zw.CreateHeader(fh)
    require.NoError(t, err)
    _, err = w.Write([]byte("* * * * * root touch /tmp/pwned"))
    require.NoError(t, err)
    require.NoError(t, zw.Close())

    r, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)

    err = archives.ExtractZipArchive(r)

    // Expected (fixed) behavior: extraction must fail, not write outside intended root
    require.Error(t, err)
    _, statErr := os.Stat(absTarget)
    assert.True(t, os.IsNotExist(statErr), "absolute-path entry should not have been written to disk")

    _ = os.Remove(absTarget) // cleanup in case current (vulnerable) behavior wrote the file
}
```
Current behavior: `ExtractZipArchive` returns `nil` and the file at `absTarget` exists, confirming the absolute path was written outside any confinement boundary — demonstrating the vulnerability.

### Citations

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

**File:** helpers/archives/zip_extract.go (L85-119)
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

func ExtractZipFile(fileName string) error {
	archive, err := zip.OpenReader(fileName)
	if err != nil {
		return err
	}
	defer func() { _ = archive.Close() }()

	return ExtractZipArchive(&archive.Reader)
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
