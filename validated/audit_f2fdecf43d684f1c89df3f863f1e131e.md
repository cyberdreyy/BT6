### Title
`extractZipFile` restores executable Git hooks into `.git/`, letting attacker-controlled cache/artifact content execute during later, higher-trust git operations - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipFile` (and its caller `ExtractZipArchive`) writes every entry name in a zip archive verbatim to disk with no destination-containment check and preserves the attacker-supplied Unix mode bits (including the executable bit). The only defense against writing into `.git/` is a log warning (`errorIfGitDirectory`/`printGitArchiveWarning`) that does not stop extraction, so a cache or artifact archive fully controlled by the pipeline author can plant an executable file such as `.git/hooks/post-checkout` that a later, runner-triggered `git` invocation in the same build directory will execute without any re-establishment of trust.

### Finding Description
`extractZipFile` extracts a `*zip.File` using `file.Name` directly as the destination path, with no `filepath.Abs`/`strings.HasPrefix` containment check against the extraction root: [1](#0-0) 

Compare this to the sibling `tarzstd` extractor, which explicitly validates that the resolved path stays inside the target directory before writing anything: [2](#0-1) 

`extractZipFile` has no equivalent check. Regular file content is written with `os.OpenFile(file.Name, ..., file.Mode().Perm())`, so the archive's own metadata controls the resulting file's executable bit: [3](#0-2) 

`ExtractZipArchive` detects `.git`-directory entries but only logs a warning ("Part of .git directory is on the list of files to extract") and continues extracting; it does not skip or reject the entry: [4](#0-3) 
This is confirmed by the existing test, which asserts the warning is printed *and* that `.git/test_file` is created afterward: [5](#0-4) 

After the copy loop, `ExtractZipArchive` also `lchmod`s every extracted path to the header's mode, so an entry such as `.git/hooks/post-checkout` can be restored with the executable bit set even if the copy step used a different default permission: [6](#0-5) 

This extraction path (`archives.ExtractZipArchive`) is reached by the "legacy zip" extractor registered for artifact/cache extraction: [7](#0-6) 
which is selected through the generic `archive.NewExtractor(format, ...)` dispatch used identically by both the cache-extractor and artifacts-downloader commands: [8](#0-7) [9](#0-8) 

Both commands extract into `os.Getwd()`, and the generated shell scripts `cd` into the job's build directory (the actual git working copy) before invoking either the cache-restore or artifact-download step: [10](#0-9) 

Attacker inputs: cache/artifact zip byte content (entry names, `..`/absolute paths, symlink targets, Unix mode bits) — all fully controlled by the pipeline author who defines `cache:`/`artifacts:` for a job. Because extraction targets the live git working copy and does not block `.git/` paths or validate destination containment, an attacker can restore `.git/hooks/<hookname>` as an executable script. Runner (or the job's own script) subsequently performs ordinary `git` operations in that same working copy (e.g., `git fetch`/`checkout`/`clean` on the next pipeline run reusing the build directory, or `git submodule` calls within the same job) which fire the hook — executing attacker bytes in a context (a later job/stage acting on the "trusted" checkout) that never re-validated the archive content.

### Impact Explanation
An unprivileged pipeline author can achieve code execution during later git operations performed by Runner in the same build directory, by planting an executable git hook through a cache or artifact archive whose content is entirely author-controlled. Where the build directory is reused across jobs/pipelines on a runner (shell/persistent executors, `GIT_STRATEGY=fetch`), this converts a same-job trust boundary into cross-job/cross-pipeline execution on the next git-touching stage, without further validation — matching the "stronger-context execution" impact class in scope.

### Likelihood Explanation
Feasible and repeatable: it requires only (1) a job able to define `cache:`/`artifacts:` and control the archived tree (trivial — e.g. `mkdir -p .git/hooks && printf '#!/bin/sh\n...' > .git/hooks/post-checkout && chmod +x .git/hooks/post-checkout` inside the job before caching/archiving that path), and (2) a subsequent runner-driven `git` command against the same working copy, which happens routinely with `GIT_STRATEGY=fetch`/`clone` reuse on shell or persistent executors. The `.git` check already exists in code (showing the maintainers are aware of this class of risk) but is warn-only, so no additional bypass is even needed.

### Recommendation
- In `extractZipFile`/`ExtractZipArchive`, add the same containment check used by the tarzstd extractor: resolve `filepath.Join(dir, file.Name)` against the extraction root and reject entries that escape it (zip-slip protection), also applying to `extractZipSymlinkEntry`'s target.
- Change `errorIfGitDirectory` from a warning into a hard rejection (skip or fail) for entries under `.git/`, rather than allowing the write to proceed.
- Strip or ignore the executable bit (and other unsafe mode bits) from zip metadata when restoring files under version-control-sensitive paths, or disable git hooks (`core.hooksPath=/dev/null` / `--no-verify` equivalents, `GIT_CONFIG_COUNT` override) for any git invocation performed by Runner against job build directories.

### Proof of Concept
Go unit test (extend `helpers/archives/zip_extract_test.go`):
```go
func TestExtractZipFile_PlantsExecutableGitHook(t *testing.T) {
    testOnArchive(t, func(t *testing.T, archive *zip.Writer) {
        hdr := &zip.FileHeader{Name: ".git/hooks/post-checkout"}
        hdr.SetMode(0o755) // executable, attacker-controlled
        w, err := archive.CreateHeader(hdr)
        require.NoError(t, err)
        _, err = w.Write([]byte("#!/bin/sh\necho PWNED > /tmp/pwned\n"))
        require.NoError(t, err)
    }, func(t *testing.T, fileName string) {
        err := ExtractZipFile(fileName)
        require.NoError(t, err) // currently succeeds despite the .git warning

        info, statErr := os.Stat(".git/hooks/post-checkout")
        require.NoError(t, statErr)
        // Assert the file was written and is executable — proving the
        // warn-only .git guard did not stop the hook from being planted.
        assert.NotZero(t, info.Mode().Perm()&0o111)
        defer os.RemoveAll(".git")
    })
}
```
Expected: today this test passes (hook file is created and executable), demonstrating the bug. After the fix, `.git/`-targeted entries should be rejected/skipped, or the extraction should fail, and the assertion for the hook file's existence should fail instead.

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

**File:** helpers/archives/zip_extract.go (L98-107)
```go
	for _, file := range archive.File {
		if err := lchmod(file.Name, file.Mode()); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}

		// Process zip metadata
		if err := processZipExtra(&file.FileHeader); tracker.actionable(err) {
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L24-33)
```go
// Extract extracts files from the reader to the directory passed to
// NewZipExtractor.
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
}
```

**File:** commands/helpers/cache_extractor.go (L646-660)
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

**File:** shells/abstract.go (L1348-1369)
```go
func (b *AbstractShell) writeRestoreCacheScript(
	ctx context.Context,
	w ShellWriter,
	info common.ShellScriptInfo,
) error {
	b.writeExports(w, info)
	b.writeCdBuildDir(w, info)

	// Try to restore from main cache, if not found cache for default branch
	return b.cacheExtractor(ctx, w, info)
}

func (b *AbstractShell) writeDownloadArtifactsScript(
	_ context.Context,
	w ShellWriter,
	info common.ShellScriptInfo,
) error {
	b.writeExports(w, info)
	b.writeCdBuildDir(w, info)

	return b.downloadAllArtifacts(w, info)
}
```
