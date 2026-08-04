### Title
`.git` warning in zip extraction is non-blocking, allowing cache/artifact archives to write into `.git/hooks/` and persist a malicious git hook - ([File: helpers/archives/zip_extract.go])

### Summary
`ExtractZipArchive` calls `errorIfGitDirectory` only to log a warning (`printGitArchiveWarning`) via the actionable-error tracker, and unconditionally proceeds to call `extractZipFile` regardless of the check's result. There is no enforcement that blocks writes to `.git/...` paths, so a crafted cache/artifact zip entry named e.g. `.git/hooks/pre-commit` will be written to disk in the job's working directory.

### Finding Description
In `ExtractZipArchive`, for every zip entry the code does: [1](#0-0) 
`errorIfGitDirectory` (in `path_check_helper.go`) merely detects whether the entry's first path component is `.git` and returns a `*os.PathError` if so; it performs no filesystem action and does not stop the loop: [2](#0-1) 
The returned error is fed into `tracker.actionable(err)`, which only decides whether to log the warning once per `Op` (deduplication), and its return value has zero bearing on whether `extractZipFile` executes: [3](#0-2) 
Regardless of that check's outcome, `extractZipFile` is always invoked next and will `MkdirAll` the parent directories and write the file content (or create a symlink, honoring the zip entry's mode bits) using `file.Name` verbatim as the destination path: [4](#0-3) [5](#0-4) 
This is confirmed by the existing test `TestExtractZipFileWithGitPath`, which explicitly asserts that `ExtractZipFile` returns no error and that `.git/test_file` is created on disk after extraction, alongside the warning log — i.e., the "protection" is documented and tested to be warning-only, not blocking: [6](#0-5) 

This function is reachable from both artifact and cache restoration flows: `commands/helpers/artifacts_downloader.go` downloads and extracts artifact zips using the working directory as the extraction root, and `ziplegacy.extractor.Extract` calls straight into `archives.ExtractZipArchive` without further path filtering: [7](#0-6) [8](#0-7) 
The same `ExtractZipArchive` code path is used for cache restoration (`commands/helpers/cache_extractor.go`), which is the more directly attacker-relevant flow since job-defined `cache:` paths and archive contents are effectively pipeline-author-controlled and restored into the job's build directory, which typically already contains a `.git` checkout from `git-checkout`/`git fetch`.

Because `file.Mode()` on the zip entry (attacker-controlled via `CreateHeader`) also determines whether the entry is written as a regular file with executable permission bits (`extractZipFileEntry` uses `file.Mode().Perm()` for `os.OpenFile`), an attacker can set the mode to `0755` so the resulting `.git/hooks/pre-commit` (or `pre-push`, `post-checkout`, etc.) file is executable immediately upon extraction, with no `chmod` step required.

### Impact Explanation
If a job can control the contents of a cache or artifact zip that is later restored into a workspace that reuses an existing `.git` checkout (e.g., cache shared across pipelines/branches on the same runner, or artifacts restored into a directory later reused for `git` operations), the attacker can write an executable file to `.git/hooks/pre-commit` (or another hook name). Git will subsequently execute that hook file whenever the corresponding git operation runs in that repository (e.g., on `git commit`, `git push`, or `git checkout`, depending on the hook). If the runner or a later job in the same workspace performs any git operation that triggers that hook, the injected script executes with the privileges of the process performing the git operation — persisting attacker-controlled command execution across job boundaries within the same workspace, which matches the scoped impact of "persistent command execution surviving job boundary via poisoned git hook."

### Likelihood Explanation
Feasibility depends on workspace reuse: GitLab Runner's default `GIT_STRATEGY=fetch` reuses the existing checkout directory (including its `.git` directory) across jobs on the same runner/executor when `GIT_CLEAN_FLAGS`/`GIT_STRATEGY` don't force a fresh clone, and cache/artifacts are restored into that same working directory before the git fetch/checkout step in some configurations, or restored and left in place for subsequent job stages within a pipeline running on the same runner. An attacker (a normal pipeline author) fully controls: (1) the contents of files they cache/upload as artifacts (they can craft an arbitrary zip with a `.git/hooks/...` entry using standard `cache`/`artifacts:paths` config pointing at a `.git/hooks` file, or a hand-crafted archive if direct download/zip crafting is possible), and (2) the executable bit via file permissions before caching. The known limiting factor is that a hook only executes upon a **subsequent git operation** in that same directory, which requires the workspace to be reused (shared runner with persistent workspace, not container/ephemeral executors that clone fresh each time) — so this is a real but executor/config-dependent path, most impactful with `shell`/`ssh`/persistent Docker-volume executors using `GIT_STRATEGY=fetch`.

### Recommendation
Make `errorIfGitDirectory` (or an equivalent check) actually block extraction of `.git`-prefixed entries during zip/tar extraction rather than only warning: skip writing the entry (do not call `extractZipFile`/`extractTarFile`) when `errorIfGitDirectory` returns non-nil, and surface this as a hard failure or at minimum a skipped-file warning rather than proceeding to write. This should be applied consistently across all archive extractors (`zip_extract.go`, and the tar/zstd equivalents) used for both cache and artifact restoration.

### Proof of Concept
Extend the existing `helpers/archives/zip_extract_test.go` test to assert non-execution instead of the current warning-only behavior:
```go
func TestExtractZipFileRefusesGitHooksWrite(t *testing.T) {
    testOnArchive(t, func(t *testing.T, archive *zip.Writer) {
        fh := &zip.FileHeader{Name: ".git/hooks/pre-commit"}
        fh.SetMode(0o755)
        w, err := archive.CreateHeader(fh)
        require.NoError(t, err)
        _, err = io.WriteString(w, "#!/bin/sh\ntouch /tmp/pwned\n")
        require.NoError(t, err)
    }, func(t *testing.T, fileName string) {
        err := ExtractZipFile(fileName)
        require.NoError(t, err)

        // Current (buggy) behavior: file gets created and is executable.
        // Expected (fixed) behavior: file must NOT exist.
        _, statErr := os.Stat(".git/hooks/pre-commit")
        assert.True(t, os.IsNotExist(statErr), ".git/hooks/pre-commit must not be created by archive extraction")
    })
}
```
Running this against the current code shows `.git/hooks/pre-commit` is created (test fails on the `assert.True` line), confirming the vulnerability; after the fix the file must not be written, and the assertion should pass.

### Citations

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

**File:** helpers/archives/zip_extract.go (L88-96)
```go
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

**File:** helpers/archives/path_error_tracker.go (L17-35)
```go
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

**File:** helpers/archives/zip_extract_test.go (L69-92)
```go
func TestExtractZipFileWithGitPath(t *testing.T) {
	testOnArchive(t, createArchiveWithGitPath, func(t *testing.T, fileName string) {
		output := logrus.StandardLogger().Out
		var buf bytes.Buffer
		logrus.SetOutput(&buf)
		defer logrus.SetOutput(output)

		err := ExtractZipFile(fileName)
		require.NoError(t, err)

		assert.Contains(t, buf.String(), "Part of .git directory is on the list of files to extract")

		stat, err := os.Stat(".git/test_file")
		assert.False(t, os.IsNotExist(err), "Expected .git/test_file to exist")
		if !os.IsNotExist(err) {
			assert.NoError(t, err)
		}

		if stat != nil {
			defer os.Remove(".git/test_file")
			assert.Equal(t, int64(13), stat.Size())
		}
	})
}
```

**File:** commands/helpers/artifacts_downloader.go (L125-140)
```go
	f, size, format, err := openArchive(file.Name())
	if err != nil {
		logrus.Fatalln(err)
	}
	defer f.Close()

	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	// Extract artifacts file
	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
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
