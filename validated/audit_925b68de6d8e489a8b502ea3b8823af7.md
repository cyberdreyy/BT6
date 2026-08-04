### Title
Symlinked intermediate directory inside job workspace bypasses artifact path containment check, allowing `CreateGzipArchive` to read files outside the job root - ([File: commands/helpers/file_archiver.go])

### Summary
`fileArchiver.process` in `commands/helpers/file_archiver.go` validates that an artifact path stays inside the job's working directory using purely lexical operations (`filepath.Abs` + `filepath.Rel`), never resolving symlinks with `filepath.EvalSymlinks`. If a job creates a symlinked *directory* inside the workspace that points outside of it, a path like `symlinked-dir/target-file` textually appears to be under `c.wd` and passes the containment check, even though the underlying file lives outside the workspace. The path is then archived by `CreateGzipArchive` (`helpers/archives/gzip_create.go`), which opens and streams the file's real content into the produced cache/artifact archive.

### Finding Description
The relevant containment check is in `fileArchiver.process`: [1](#0-0) 
It computes `absolute = filepath.Abs(match)` and `relative = filepath.Rel(c.wd, absolute)`, then rejects the path only if the resulting *string* starts with `".."`. Neither `filepath.Abs` nor `filepath.Rel` dereference symlinks — they operate purely on the path string. So if a job creates, e.g., `workdir/link -> /etc` (a symlinked directory, not a symlinked file) and the pipeline references `link/passwd` as an artifact path, the lexical path `workdir/link/passwd` is computed as being under `c.wd`, and the check passes.

`processPath` uses `doublestar.FilepathGlob(rel, doublestar.WithNoFollow())` to expand the glob and `filepath.Walk` to enumerate matches: [2](#0-1) 
`WithNoFollow` only prevents the glob's *wildcard* expansion from descending into symlinked directories and rejects a final path component that is itself a symlink; it does not protect a literal (non-wildcard), pattern-free intermediate segment such as `link/passwd`, nor does `filepath.Walk`/`os.Lstat` on the final component detect that an earlier path segment was a symlink — `os.Lstat("workdir/link/passwd")` transparently follows the `link` directory component and returns info for the real target file, which is a regular file, not a symlink.

`add()` then stores this path using `os.Lstat`: [3](#0-2) 
Finally, `CreateGzipArchive` receives this file name and, since `os.Lstat` reports a regular file, calls `writeGzipFile`, which `os.Open`s and streams the real (outside-workspace) file content into the gzip archive: [4](#0-3) 

The only symlink-related test, `TestGzipArchivingShouldFailIfSymlinkIsBeingArchived`, only covers the case where the *final* path argument passed to `CreateGzipArchive` is itself a symlink (rejected because `os.Lstat` returns `Mode()&ModeSymlink != 0`, so `IsRegular()` is false): [5](#0-4) 
It does not cover a symlinked *directory* used as an intermediate path component, which is the scenario in this finding, and no other check (in `file_archiver.go` or `gzip_create.go`) resolves symlinks before validating containment.

### Impact Explanation
An unprivileged pipeline author who can execute shell commands in a job (i.e., create a symlink inside `$CI_PROJECT_DIR`) and control the `artifacts:paths` or cache `paths` glob can cause the Runner to read and upload arbitrary files reachable from the executor's filesystem view (subject to OS file permissions of the process running the job) into the job's artifact/cache archive, which is then exfiltrated by simply downloading the artifact. This is a job-root escape for file reads, violating the "file operations must stay within intended build/cache/artifact roots" invariant. Actual severity depends on executor: for `shell` executor this can reach arbitrary host files readable by the runner's OS user; for containerized executors it's scoped to files readable within the container's filesystem namespace (e.g., mounted secrets, other paths on the container image) rather than the true host filesystem.

### Likelihood Explanation
Highly feasible and repeatable: it only requires two ordinary job script lines (`ln -s /target/dir link`) followed by declaring `artifacts: paths: [link/file]` or a cache path — both are entirely attacker-controlled CI config/job-script features with no special privileges. No existing GitLab Runner code path resolves symlinks before the containment check, so the bypass is deterministic, not a race or timing issue.

### Recommendation
In `fileArchiver.process` (and/or `findRelativePathInProject`), resolve the path with `filepath.EvalSymlinks` before/while performing the containment comparison against `c.wd`, and re-validate that the *resolved* real path is still a subpath of `c.wd`. Additionally, `add`/`CreateGzipArchive` could defensively re-verify with `filepath.EvalSymlinks` immediately before opening the file to close any TOCTOU window, rejecting or warning (similar to the existing "not supported: outside build directory" error) whenever the resolved real path escapes the working directory.

### Proof of Concept
Integration test (extends `commands/helpers/file_archiver_integration_test.go` style) or a new unit test in `commands/helpers`:
```go
func TestFileArchiver_SymlinkedDirectoryEscapesWorkdir(t *testing.T) {
    wd := t.TempDir()
    outside := t.TempDir()
    secret := filepath.Join(outside, "secret.txt")
    require.NoError(t, os.WriteFile(secret, []byte("TOP-SECRET"), 0o644))

    // Attacker-created symlinked directory inside the job workspace pointing outside it.
    link := filepath.Join(wd, "escape")
    require.NoError(t, os.Symlink(outside, link))

    // Simulate job cwd
    oldWd, _ := os.Getwd()
    defer os.Chdir(oldWd)
    require.NoError(t, os.Chdir(wd))

    cmd := helpers.NewCacheArchiverCommandForTest(filepath.Join(wd, "out.zip"), []string{"escape/secret.txt"})
    require.NoError(t, cmd.Execute(nil)) // or however Execute is invoked in tests

    matches := helpers.GetMatches(&cmd)
    require.Contains(t, matches, filepath.Join("escape", "secret.txt"))
    // Then confirm archive content equals "TOP-SECRET", proving read of file outside wd.
}
```
Expected (current, buggy) result: `escape/secret.txt` is accepted by `process()`, added to `c.files`, and the produced archive contains `TOP-SECRET` content, despite `secret.txt` residing entirely outside `wd`. A fixed implementation should reject the path with an error equivalent to `"not supported: outside build directory"` once symlink resolution is added.

### Citations

**File:** commands/helpers/file_archiver.go (L65-88)
```go
func (c *fileArchiver) process(match string) bool {
	var absolute, relative string
	var err error

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
	}
```

**File:** commands/helpers/file_archiver.go (L127-138)
```go
func (c *fileArchiver) add(path string) error {
	// Always use slashes
	path = filepath.ToSlash(path)

	// Check if file exist
	info, err := os.Lstat(path)
	if err == nil {
		c.files[path] = info
	}

	return err
}
```

**File:** commands/helpers/file_archiver.go (L159-178)
```go
	// Use WithNoFollow option to prevent symlink cycles during the initial glob
	matches, err := doublestar.FilepathGlob(rel, doublestar.WithNoFollow())
	if err != nil {
		logrus.Warningf("%s: %v", path, err)
		return
	}

	found := 0

	for _, match := range matches {
		err := filepath.Walk(match, func(path string, info os.FileInfo, err error) error {
			if c.process(path) {
				found++
			}
			return nil
		})
		if err != nil {
			logrus.Warningln("Walking", match, err)
		}
	}
```

**File:** helpers/archives/gzip_create.go (L24-63)
```go
func writeGzipFile(w io.Writer, fileName string, fileInfo os.FileInfo) error {
	if !fileInfo.Mode().IsRegular() {
		return fmt.Errorf("the %q is not a regular file", fileName)
	}

	gz := gzip.NewWriter(w)
	gz.Header.Name = sanitizePath(fileInfo.Name())
	gz.Header.Comment = sanitizePath(fileName)
	gz.Header.ModTime = fileInfo.ModTime()

	defer func() { _ = gz.Close() }()

	file, err := os.Open(fileName)
	if err != nil {
		return err
	}
	defer func() { _ = file.Close() }()

	_, err = io.Copy(gz, file)
	return err
}

func CreateGzipArchive(w io.Writer, fileNames []string) error {
	for _, fileName := range fileNames {
		fi, err := os.Lstat(fileName)
		if os.IsNotExist(err) {
			logrus.Warningln("File ignored:", err)
			continue
		} else if err != nil {
			return err
		}

		err = writeGzipFile(w, fileName, fi)
		if err != nil {
			return err
		}
	}

	return nil
}
```

**File:** helpers/archives/gzip_create_test.go (L72-86)
```go
func TestGzipArchivingShouldFailIfSymlinkIsBeingArchived(t *testing.T) {
	dir := t.TempDir()

	filePath := filepath.Join(dir, "file")
	err := os.WriteFile(filePath, testGzipFileContent, 0o644)
	require.NoError(t, err)

	symlinkPath := filepath.Join(dir, "symlink")
	err = os.Symlink(filePath, symlinkPath)
	require.NoError(t, err)

	var buffer bytes.Buffer
	err = CreateGzipArchive(&buffer, []string{filePath, symlinkPath})
	require.Errorf(t, err, "the %q is not a regular file", symlinkPath)
}
```
