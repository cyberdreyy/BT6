### Title
Symlinked directory components bypass workspace-containment check, letting `artifacts:paths`/`cache:paths` archive files outside the job workspace - ([File: commands/helpers/file_archiver.go], [File: helpers/archives/zip_create.go])

### Summary
`fileArchiver.process`/`findRelativePathInProject` validate that an artifact path is "inside" the working directory using purely lexical `filepath.Abs`/`filepath.Rel` string comparisons, never resolving symlinks in intermediate path components. If a pipeline author creates a symlinked directory inside the workspace pointing outside of it (e.g. `link -> /etc`), a literal artifact path like `link/passwd` passes the containment check, and `os.Lstat`/`os.Open` in `createZipEntry`/`createZipFileEntry` transparently follow that intermediate symlink and read the real out-of-workspace file content into the produced artifact zip.

### Finding Description
`fileArchiver.process` in [1](#0-0)  computes containment via `filepath.Abs`/`filepath.Rel` on the literal string path and only rejects paths whose relative form starts with `..`. It never calls `filepath.EvalSymlinks` or otherwise verifies that no path *component* is a symlink leading outside `c.wd`. `findRelativePathInProject` (used by `processPath`) has the same lexical-only containment logic in [2](#0-1) .

For a literal (non-glob) artifact path such as `link/passwd` where `link` is a symlink created by the job (e.g. `ln -s /etc link` in a `before_script`), `doublestar.FilepathGlob(rel, doublestar.WithNoFollow())` in [3](#0-2)  simply returns the literal path unchanged (no directory expansion is needed, so `WithNoFollow` — which only guards against following symlinked directories during wildcard expansion — never comes into play). `filepath.Walk` then invokes `c.process` on that literal path, which passes the lexical containment check described above, and `c.add` performs `os.Lstat(path)` in [4](#0-3) . Because only the *final* path component is not dereferenced by `Lstat`, the intermediate `link` component is followed by the OS, so the returned `os.FileInfo` describes the real target file (e.g. `/etc/passwd`) as an ordinary regular file, not a symlink.

That entry is later handed to `CreateZipArchive`/`createZipEntry` in [5](#0-4) . `os.Lstat(fileName)` again follows the intermediate symlink and reports a regular file, so control flows to `createZipFileEntry`, which calls `os.Open(fh.Name)` and `io.Copy` in [6](#0-5) , copying the real content of the out-of-workspace file into the archive under the name `link/passwd`.

Note this differs from the “final-component symlink” scenario suggested in the question: when the *last* path segment itself is a symlink, `os.Lstat` correctly reports `os.ModeSymlink` and `createZipSymlinkEntry` only stores the link-target string (no dereference), so that specific case is safe. The actual bypass is via a symlinked *intermediate directory component*, which the lexical containment check in `process`/`findRelativePathInProject` never accounts for.

### Impact Explanation
An unprivileged pipeline author can cause the runner to package arbitrary files readable by the job/runner process (subject to OS/container permissions) into a job artifact, even though those files live outside the project's build directory. The artifact is then uploaded to GitLab and becomes downloadable by anyone with artifact-read access to that project/pipeline, which is a broader audience than "processes with OS read access to the runner host." This violates the invariant that "file operations must stay within intended build/cache/artifact roots" and can leak runner-host or shared-filesystem content (e.g. other checked-out repositories, credential files readable by the runner user, shared cache directories) into a project-scoped artifact.

### Likelihood Explanation
Fully reachable with normal, unprivileged CI job capabilities: the attacker only needs a `before_script`/`script` step that creates a symlink inside the workspace and a `.gitlab-ci.yml` `artifacts:paths` (or `cache:paths`) entry naming a literal path through that symlink. No admin privileges, no executor misconfiguration, and no glob syntax are required, so the containment check's blind spot is trivially and repeatably triggered. Feasibility is bounded only by the OS/container-level file permissions of the job process, which is the same trust boundary the runner already relies on for regular file access within the job.

### Recommendation
In `fileArchiver.process` and `findRelativePathInProject`, resolve the path with `filepath.EvalSymlinks` (or walk and verify each component) before/while performing the containment comparison against `c.wd`, rejecting any path whose resolved real path is not a sub-path of the resolved real working directory. Alternatively, reject any path where an intermediate component is a symlink (`os.Lstat` each ancestor directory of the target) unless it resolves within `c.wd`.

### Proof of Concept
Go integration test sketch for `commands/helpers/file_archiver.go`:
```go
func TestFileArchiver_RejectsPathThroughSymlinkedDirectory(t *testing.T) {
    wd := t.TempDir()
    outside := t.TempDir()
    secret := filepath.Join(outside, "secret.txt")
    require.NoError(t, os.WriteFile(secret, []byte("TOPSECRET"), 0644))

    require.NoError(t, os.Symlink(outside, filepath.Join(wd, "link")))

    origWd, _ := os.Getwd()
    require.NoError(t, os.Chdir(wd))
    defer os.Chdir(origWd)

    c := &fileArchiver{Paths: []string{"link/secret.txt"}}
    require.NoError(t, c.enumerate())

    // Assert the archiver did NOT capture a file outside wd
    for f := range c.files {
        abs, _ := filepath.Abs(f)
        real, _ := filepath.EvalSymlinks(abs)
        require.True(t, strings.HasPrefix(real, wd), "captured file %s resolves outside workspace: %s", f, real)
    }
}
```
Expected current (buggy) result: `c.files` contains `link/secret.txt`, whose real path resolves to `outside/secret.txt`, failing the assertion — demonstrating that `CreateZipArchive` fed with `c.sortedFiles()` would embed `TOPSECRET` content from outside the workspace into the produced zip.

### Citations

**File:** commands/helpers/file_archiver.go (L65-101)
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

	if err == nil {
		return true
	}

	if os.IsNotExist(err) {
		// We hide the error that file doesn't exist
		return false
	}

	logrus.Warningf("%s: %v", match, err)
	return false
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

**File:** commands/helpers/file_archiver.go (L160-164)
```go
	matches, err := doublestar.FilepathGlob(rel, doublestar.WithNoFollow())
	if err != nil {
		logrus.Warningf("%s: %v", path, err)
		return
	}
```

**File:** commands/helpers/file_archiver.go (L191-222)
```go
func (c *fileArchiver) findRelativePathInProject(path string) (string, error) {
	slashPath := filepath.ToSlash(path)
	if filepath.Clean(slashPath) == filepath.Clean(c.wd) {
		return ".", nil
	}

	base, patt := slashPath, ""
	// check if path contains a glob pattern
	if strings.ContainsAny(slashPath, "*?[{") {
		base, patt = doublestar.SplitPattern(slashPath)
	}

	abs, err := filepath.Abs(base)
	if err != nil {
		return "", fmt.Errorf("could not resolve artifact absolute path %s: %w", path, err)
	}

	rel, err := filepath.Rel(c.wd, abs)
	if err != nil {
		return "", fmt.Errorf("could not resolve artifact relative path %s: %w", path, err)
	}

	// If fully resolved relative path begins with ".." it is not a subpath of our working directory
	if strings.HasPrefix(rel, ".."+string(filepath.Separator)) || rel == ".." {
		return "", fmt.Errorf("artifact path is not a subpath of project directory: %s", path)
	}

	// Relative path is needed now that our fsys "root" is at the working directory
	rel = filepath.Join(rel, patt)
	rel = filepath.FromSlash(rel)
	return rel, nil
}
```

**File:** helpers/archives/zip_create.go (L32-50)
```go
func createZipFileEntry(archive *zip.Writer, fh *zip.FileHeader) error {
	fh.Method = zip.Deflate
	fw, err := archive.CreateHeader(fh)
	if err != nil {
		return err
	}

	file, err := os.Open(fh.Name)
	if err != nil {
		return err
	}

	_, err = io.Copy(fw, file)
	_ = file.Close()
	if err != nil {
		return err
	}
	return nil
}
```
