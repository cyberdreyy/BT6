### Title
Zip extraction writes files using unsanitized `zip.File.Name`, allowing path traversal / absolute-path writes outside the extraction root - (File: `helpers/archives/zip_extract.go`)

### Summary
`extractZipFileEntry`, `extractZipSymlinkEntry`, `extractZipDirectoryEntry`, and `extractZipFile` in `helpers/archives/zip_extract.go` call `os.OpenFile`, `os.Symlink`, `os.Mkdir`, and `os.MkdirAll` directly on `file.Name` from the zip archive with no validation that the resolved path stays within the intended destination directory. The only path-related check present, `errorIfGitDirectory` in `helpers/archives/path_check_helper.go`, only rejects `.git` entries and does nothing to prevent `../` traversal or absolute paths.

### Finding Description
`ExtractZipArchive` [1](#0-0)  iterates `archive.File` and, for each entry, only runs `errorIfGitDirectory(file.Name)` before calling `extractZipFile(file)`. `errorIfGitDirectory` / `isPathAGitDirectory` [2](#0-1)  merely checks whether the first path component (after `filepath.Clean`) is `.git`; it performs no root-confinement check, so a name like `../../evil` or `/etc/passwd` or (on Windows) `C:\evil` or `\\host\share\evil` passes through untouched.

`extractZipFile` then does `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and dispatches based on file mode to `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, or `extractZipFileEntry` — all of which use `file.Name` verbatim in `os.Mkdir`, `os.Symlink`, `os.Remove`, and `os.OpenFile` [3](#0-2) . There is no `filepath.Clean`/prefix check against a destination root, and no rejection of absolute paths or `..` segments anywhere in this code path.

This confirms the classic "Zip Slip" pattern is present in the code as written. However, whether it is exploitable in a way that violates the stated scoped impact depends on how `ExtractZipFile`/`ExtractZipArchive` is invoked by the cache/artifact extraction commands (`commands/helpers/cache_extractor.go`, `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`) — specifically whether the process changes its working directory (`os.Chdir`) into a per-job, per-project temp/cache directory before calling extraction, and whether the destination directory itself is isolated per job/project rather than a single shared directory that could contain another project's files reachable via relative traversal. I was not able to fully trace the calling code (`cache_extractor.go`, `zip_legacy_extractor.go`) in this session to confirm exactly what working directory is active at extraction time or whether the runner ever passes attacker-controlled absolute destination handling, so I cannot conclusively confirm the specific "cross-project shared cache volume" impact claimed in the question — only the underlying missing-path-validation bug in `zip_extract.go` itself.

### Impact Explanation
At minimum, this is an unsanitized path/Zip-Slip vulnerability: any zip processed via `ExtractZipArchive` (used for both artifacts and cache archives, since a job author fully controls the content of files they upload as artifacts, which are later downloaded by other jobs, and controls cache archive contents restored into their own job) can write/overwrite files outside the intended extraction directory via `../` sequences or absolute paths, subject to file-system permissions of the process running the extraction (e.g., `gitlab-runner-helper` / build process). If the extraction root is shared across projects/jobs on the same host (as the question's precondition states for shell/shared-cache setups), this allows writing into another project's checkout or cache directory, i.e., cross-job/cross-project file tampering — a genuine isolation-boundary bug, not merely a theoretical or admin-configuration issue.

### Likelihood Explanation
Feasible and repeatable: an unprivileged pipeline author fully controls artifact and cache zip contents (via `CreateZipArchive` or by crafting a raw zip with a tool like `evilarc`/manual zip construction), and the runner's own extraction routine (`ExtractZipFile` → `ExtractZipArchive` → `extractZipFile`) performs no validation before writing. It applies most directly to shared-host executors (shell executor, or docker/kubernetes with shared volumes) where the extraction root is reused across jobs — a precondition explicitly given in the question and consistent with how `.git` protection was added for a narrower reason (to avoid corrupting `.git` during extraction) but broader traversal was never addressed. I could not verify from the available files whether the calling commands additionally `chdir` into a properly isolated directory that would limit the practical blast radius, which is the main remaining uncertainty.

### Recommendation
In `extractZipFile` (and the analogous directory/symlink/file entry functions) in `helpers/archives/zip_extract.go`, resolve each `file.Name` against the destination root with `filepath.Clean`/`filepath.Join`, verify the resulting path has the destination root as a prefix (using `filepath.Rel` and rejecting results starting with `..` or being absolute), and reject/skip entries that fail this check — mirroring the standard Zip Slip mitigation. Apply the same check before creating symlinks, ensuring the symlink target also cannot point outside the extraction root.

### Proof of Concept
```go
func TestExtractZipArchive_PathTraversal(t *testing.T) {
    dir := t.TempDir()
    zipPath := filepath.Join(dir, "evil.zip")

    f, _ := os.Create(zipPath)
    zw := zip.NewWriter(f)
    w, _ := zw.Create("../../outside.txt")
    _, _ = w.Write([]byte("pwned"))
    _ = zw.Close()
    _ = f.Close()

    extractDir := filepath.Join(dir, "job-root")
    _ = os.MkdirAll(extractDir, 0o755)

    cwd, _ := os.Getwd()
    _ = os.Chdir(extractDir)
    defer os.Chdir(cwd)

    err := archives.ExtractZipFile(zipPath)
    require.NoError(t, err)

    // Assert the file was NOT written outside extractDir
    _, statErr := os.Stat(filepath.Join(dir, "outside.txt"))
    assert.True(t, os.IsNotExist(statErr), "path traversal allowed write outside extraction root")
}
```
Expected (current buggy) behavior: `outside.txt` is created two directories above `extractDir`, proving the traversal; a fixed implementation should either error out or confine the write inside `extractDir`.

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

**File:** helpers/archives/zip_extract.go (L85-97)
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
