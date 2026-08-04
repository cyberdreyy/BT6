### Title
Zip archive extraction writes into `.git` control paths (hooks/config) despite "unsafe path" detection - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipSymlinkEntry` and `extractZipFileEntry` write archive entries directly to `file.Name` with no path containment or repo-control-path enforcement, and the only "protection" (`errorIfGitDirectory` in `helpers/archives/path_check_helper.go`) merely logs a warning without blocking extraction. A malicious artifact/cache zip can therefore restore files (including symlinks) into `.git/hooks/*`, `.git/config`, or other repo-control paths of the job's working directory.

### Finding Description
`ExtractZipArchive` (helpers/archives/zip_extract.go:85-110) calls `errorIfGitDirectory(file.Name)` for every entry, but the resulting `*os.PathError` is only used to print a warning via `printGitArchiveWarning` — it is never returned as a fatal error or used to skip the entry: [1](#0-0) 
`extractZipFile` then unconditionally proceeds to call `extractZipFileEntry` or `extractZipSymlinkEntry`, both of which `os.Remove(file.Name)` and recreate the entry (as a regular file or as a symlink pointing at attacker-controlled target data) at whatever path is embedded in the zip: [2](#0-1) 
The existing test `TestExtractZipFileWithGitPath` explicitly documents this behavior: extraction of a `.git/test_file` entry succeeds (`require.NoError`), the file is verified to exist on disk afterward, and only a warning log line is asserted: [3](#0-2) 
`isPathAGitDirectory` (helpers/archives/path_check_helper.go:13-19) only flags paths whose *first* cleaned path segment is literally `.git` — it is a logging heuristic, not an extraction guard, and it can't stop a symlink entry crafted at another name that later resolves into `.git` on subsequent traversal, or an entry with case/separator variants on certain filesystems.

Both `ArtifactsDownloaderCommand.Execute` and `CacheExtractorCommand.Execute` invoke the zip/tar extractor with `wd` = the job's working directory (i.e., the checked-out git repository root): [4](#0-3) [5](#0-4) 
Both artifact contents (produced by an earlier stage/job that an attacker fully controls, e.g., `job.artifacts.paths`) and cache contents (keyed by attacker-controlled `cache.key`) are attacker-influenced inputs that flow, unsanitized, into this extraction path. This means a pipeline author can craft a job that produces an artifact or cache archive containing entries such as `.git/hooks/post-checkout`, `.git/config`, or a symlink entry named e.g. `link` → `../.git/hooks/post-checkout`, and have a downstream job (same or later stage, same workspace) extract that archive directly on top of its checked-out repository.

The runner does have a separate, unrelated mitigation: at the "beginning of every build" it removes git lock files and the `post-checkout` hook, and optionally (`clean_git_config`) removes `.git/config` — this was introduced specifically to close previous incidents of this class (MR 5438, documented in `docs/configuration/advanced-configuration.md`, "Cleaning Git configuration"). However:
- This cleanup runs at the start of the *get_sources* stage (checkout), i.e., **before** the artifact/cache-restore stage, not after it. Artifact/cache extraction in the standard job stage order occurs *after* `get_sources`, so any hook/config planted by cache/artifact extraction during the current job is not removed until the *next* job's checkout runs.
- Within the same job, after cache/artifact restoration plants a malicious `.git/hooks/post-checkout`, any subsequent `git checkout`/`git submodule update` invoked later in that same job's `script:` (or by the runner itself, e.g., `GIT_STRATEGY=fetch` combined with submodules) executes attacker-controlled hook code before any subsequent-job cleanup would run.
- The pre-checkout cleanup mitigates the persistent-workspace, cross-job hook-execution risk to a real but narrower window; it does not change the fact that `extractZipSymlinkEntry`/`extractZipFileEntry` themselves perform no path/target validation and will happily overwrite `.git/config`, `.gitattributes`, `.gitmodules`, or plant a symlink escaping the repo root via `../` targets (since symlink target strings are taken verbatim from the archive with no cleaning), and `clean_git_config` (the setting that removes `.git/config`) is opt-in, not default.

### Impact Explanation
An unprivileged pipeline author who controls the contents of an artifact or cache archive can cause a same-job or later-job git operation to execute attacker-supplied hook code (e.g., `.git/hooks/post-checkout`) or trust attacker-modified `.git/config` (e.g., `core.fsmonitor`, `credential.helper`, `insteadOf` URL rewrites) within the runner's build context. This can lead to code execution in the build/helper container beyond the artifact/cache stage, or credential/URL redirection affecting subsequent git fetch/clone/checkout operations performed by the runner or job script, matching the "protected-ref escalation or credential misuse via repo-control overwrite" impact category.

### Likelihood Explanation
Feasibility is high and fully within an unprivileged pipeline author's reach: crafting a `.zip` cache/artifact with a `.git/hooks/post-checkout` entry or symlink entry requires no special privileges — only control over a job's `script`/`cache`/`artifacts` config, which any user who can edit `.gitlab-ci.yml` or push a branch has. Repeatability is deterministic (not opportunistic/racy): the extraction unconditionally writes the entry every time the archive is restored. The only gating factor is whether the job configuration causes later git operations in the same or a subsequent job on the same persistent workspace to actually invoke the hook or read the tampered config, which is common in shell/SSH executors with persistent build directories and submodules.

### Recommendation
Make `errorIfGitDirectory` (and equivalent tar/zstd extractors) a hard extraction-blocking check rather than a warning-only log: skip or fail the entry instead of writing it. Additionally, validate that the resolved absolute path of every extracted entry (including symlink targets) stays within the intended extraction root (`chroot`-style check similar to `commands/helpers/archive/tarzstd/tarzstd_extractor.go:57-64`), and reject entries whose target or name references `.git` anywhere in the path (not just as the first segment), reject absolute paths and `..` traversal, and reject symlink entries whose target escapes the working directory or points at `.git` control paths, for all archive formats (zip, tar, tar.zst, legacy zip).

### Proof of Concept
Go unit test in `helpers/archives/zip_extract_test.go`:
```go
func TestExtractZipFileDoesNotWriteGitHook(t *testing.T) {
    testOnArchive(t, func(t *testing.T, archive *zip.Writer) {
        hook, err := archive.Create(".git/hooks/post-checkout")
        require.NoError(t, err)
        _, err = io.WriteString(hook, "#!/bin/sh\ntouch /tmp/pwned\n")
        require.NoError(t, err)
    }, func(t *testing.T, fileName string) {
        err := ExtractZipFile(fileName)
        require.NoError(t, err) // currently succeeds

        // Assert the hook was NOT written — this currently FAILS,
        // proving the archive extractor writes into .git control paths.
        _, statErr := os.Stat(".git/hooks/post-checkout")
        assert.True(t, os.IsNotExist(statErr),
            "expected .git/hooks/post-checkout to not be restored from an untrusted archive")
    })
}
```
Expected today: the test fails because the file is written (matching the existing `TestExtractZipFileWithGitPath` behavior which asserts the file *does* exist). A PoC CI pipeline: job A produces an artifact zip containing `.git/hooks/post-checkout`; job B (same runner, persistent workspace/shell executor, or same job with a later `git submodule update`) restores the artifact and then performs a git checkout/submodule operation — hook execution (e.g., writing a marker file) confirms code execution via the restored control path.

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

**File:** commands/helpers/artifacts_downloader.go (L88-140)
```go
func (c *ArtifactsDownloaderCommand) Execute(cliContext *cli.Context) {
	log.SetRunnerFormatter()

	wd, err := os.Getwd()
	if err != nil {
		logrus.Fatalln("Unable to get working directory")
	}

	if c.URL == "" {
		logrus.Warningln("Missing URL (--url)")
	}
	if c.Token == "" {
		logrus.Warningln("Missing runner credentials (--token)")
	}
	if c.ID <= 0 {
		logrus.Warningln("Missing build ID (--id)")
	}
	if c.ID <= 0 || c.Token == "" || c.URL == "" {
		logrus.Fatalln("Incomplete arguments")
	}

	// Create temporary file
	file, err := os.CreateTemp(c.StagingDir, "artifacts")
	if err != nil {
		logrus.Fatalln(err)
	}
	_ = file.Close()
	defer func() { _ = os.Remove(file.Name()) }()

	// Download artifacts file
	err = c.doRetry(func(retry int) error {
		return c.download(file.Name(), retry)
	})
	if err != nil {
		logrus.Fatalln(err)
	}

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

**File:** commands/helpers/cache_extractor.go (L618-663)
```go
func (c *CacheExtractorCommand) Execute(cliContext *cli.Context) {
	log.SetRunnerFormatter()

	c.normalizeExtractorArgs()
	if err := validateCacheTransferTuning(c.TransferBufferSize, c.ChunkSize, c.Concurrency); err != nil {
		logrus.Fatalln(err)
	}

	wd, err := os.Getwd()
	if err != nil {
		logrus.Fatalln("Unable to get working directory")
	}

	if c.File == "" {
		warningln("Missing cache file")
	}

	if c.URL != "" || c.GoCloudURL != "" {
		err := c.doRetry(c.download)
		if err != nil {
			warningln(err)
		}
	} else {
		logrus.Infoln(
			"No URL provided, cache will not be downloaded from shared cache server. " +
				"Instead a local version of cache will be extracted.")
	}

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
