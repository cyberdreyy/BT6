### Title
Artifact restore writes attacker-controlled entries into `.git/` control paths with no enforcing block, only a bypassable warning - (File: helpers/archives/zip_extract.go, commands/helpers/artifacts_downloader.go)

### Summary
Artifacts produced by an earlier pipeline job are fully attacker-controlled (file names, contents, archive format) and are restored by a later dependent job's `gitlab-runner artifacts-downloader` step by extracting the archive directly into the job's working directory. The only `.git`-path safeguard that exists in the codebase (`errorIfGitDirectory` in `helpers/archives/path_check_helper.go`) is a **warning-only** check that does not block the write, and it is exercised only by the legacy `helpers/archives` zip extractor — the production artifact-download extraction path (`commands/helpers/archive` + fastzip, invoked from `commands/helpers/artifacts_downloader.go`) does not appear to apply this or any equivalent `.git` filtering at all.

### Finding Description
`network/gitlab.go: downloadArtifactFile` fetches the raw artifact bytes for a dependency over HTTP with no content inspection; the artifact bytes are entirely produced by an earlier job the attacker controls (as pipeline author/unprivileged user). Extraction happens in `commands/helpers/artifacts_downloader.go: (*ArtifactsDownloaderCommand).Execute`, which opens the downloaded archive and calls: [1](#0-0) 
into `wd`, the job's working directory (project checkout root), using `archive.NewExtractor` from `commands/helpers/archive/archive.go`: [2](#0-1) 
This registered extractor interface has no built-in restriction preventing an archive entry named e.g. `.git/hooks/post-checkout` or `.git/config` from being written.

The only place in the codebase with a `.git`-path check is the *legacy* zip extractor used by `ExtractZipFile`/`CreateZipArchive` (not shown to be wired into the artifact-download path): [3](#0-2) 
and critically, this check only logs a warning and does not stop extraction: [4](#0-3) 
This is confirmed by the existing test, which extracts a `.git/test_file` entry and asserts the file **is created** (not rejected) after only a warning is logged: [5](#0-4) 

Separately, mitigations do exist for git-control-file hygiene, but they run at the wrong time and are not universally enabled: [6](#0-5) [7](#0-6) 
Per documentation, this cleanup runs "at the beginning and end of every build" and is disabled by default for the shell executor or `GIT_STRATEGY=none`: [8](#0-7) 
Artifact/dependency download occurs after the "get_sources" (checkout) stage completes — i.e., after the "beginning" cleanup already ran and before the job's own script executes — so malicious `.git/hooks/*` or `.git/config` content restored from an artifact is live during the entire script execution window of the dependent job, and is only removed afterward (if `clean_git_config` is enabled at all).

### Impact Explanation
A pipeline author who controls Job A's artifact contents can smuggle files into `.git/hooks/` (e.g. `post-checkout`, `pre-commit`) or `.git/config` (e.g. `core.sshCommand`, `url.insteadOf` credential/URL rewriting, `include.path`) into Job B's working tree via `needs`/`dependencies`. If Job B's script performs any git operation (commit, checkout, submodule update) or the shell/git-helper trusts `.git/config`, the attacker gains command execution or config-driven credential/URL manipulation scoped to Job B — a repo-control overwrite that can escalate to protected-ref bypass or credential misuse if Job B has elevated privileges (e.g., push access, deploy tokens) that Job A's author does not.

### Likelihood Explanation
Fully feasible for any unprivileged user able to define/run two jobs with a `needs`/`dependencies` relationship in the same pipeline — a completely standard, unprivileged CI configuration. No special runner config or executor privilege is required beyond default artifact-passing behavior; likelihood increases further on shell executors or `GIT_STRATEGY=none`, where `clean_git_config` defaults to disabled entirely.

### Recommendation
Enforce a hard rejection (not just a warning) of any archive entry path (after `filepath.Clean`) whose first component is `.git` in every extraction code path used for artifact/cache restore — including the fastzip/`commands/helpers/archive`-backed extractors used by `artifacts-downloader`, not only the legacy `helpers/archives` package. Additionally, run git-control-file cleanup (lock files, hooks, and — when configured — `.git/config`/`.git/hooks`) immediately after artifact/cache restore and immediately before job script execution, not only at job start/end.

### Proof of Concept
Go unit test targeting the actual restore path:
```go
func TestArtifactRestoreRejectsGitHookEntry(t *testing.T) {
    dir := t.TempDir()
    require.NoError(t, os.MkdirAll(filepath.Join(dir, ".git"), 0o755))

    buf := new(bytes.Buffer)
    zw := zip.NewWriter(buf)
    f, _ := zw.Create(".git/hooks/post-checkout")
    _, _ = f.Write([]byte("#!/bin/sh\ntouch /tmp/pwned\n"))
    require.NoError(t, zw.Close())

    extractor, err := archive.NewExtractor(archive.Zip, bytes.NewReader(buf.Bytes()), int64(buf.Len()), dir)
    require.NoError(t, err)
    err = extractor.Extract(context.Background())

    // Expected (secure) behavior:
    require.Error(t, err) // extraction of .git/* entries should be rejected
    _, statErr := os.Stat(filepath.Join(dir, ".git", "hooks", "post-checkout"))
    assert.True(t, os.IsNotExist(statErr), "attacker-controlled git hook must not be restored")
}
```
Pipeline-level PoC: Job A uploads an artifact zip containing `.git/hooks/post-checkout`; Job B declares `needs: [job_a]`, downloads artifacts, and its script runs `git checkout HEAD` — assert the hook script executes (e.g., writes a marker file), proving repo-control overwrite via artifact restore.

### Citations

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

**File:** commands/helpers/archive/archive.go (L99-109)
```go
// NewExtractor returns a new Extractor of the specified format.
//
// The extractor will extract files to the directory provided.
func NewExtractor(format Format, r io.ReaderAt, size int64, dir string) (Extractor, error) {
	fn := extractors[format]
	if fn == nil {
		return nil, fmt.Errorf("%q format: %w", format, ErrUnsupportedArchiveFormat)
	}

	return fn(r, size, dir)
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

**File:** functions/concrete/run/stages/get_sources.go (L555-592)
```go
// cleanupGitState removes stale lock files and (when CleanGitConfig is set)
// potentially-malicious git configs and hooks from prior jobs.
func (s GetSources) cleanupGitState(e *env.Env) {
	dotGitDir := filepath.Join(e.WorkingDir, ".git")

	// Remove lock files and stale post-checkout hook.
	lockFiles := []string{"index.lock", "shallow.lock", "HEAD.lock", "config.lock"}
	for _, f := range lockFiles {
		_ = os.Remove(filepath.Join(dotGitDir, f))
	}
	_ = os.Remove(filepath.Join(dotGitDir, "hooks", "post-checkout"))

	if s.hasSubmodules() {
		modulesDir := filepath.Join(dotGitDir, "modules")
		for _, f := range lockFiles {
			walkRemove(modulesDir, f, false)
		}
		// The old shell code also removed post-checkout recursively in modules.
		walkRemove(modulesDir, "post-checkout", false)
	}

	walkRemove(filepath.Join(dotGitDir, "refs"), ".lock", true)

	// Clean configs and hooks if requested.
	if !s.CleanGitConfig {
		return
	}

	for _, dir := range []string{filepath.Join(e.WorkingDir+".tmp", templateDirName), dotGitDir} {
		_ = os.Remove(filepath.Join(dir, "config"))
		_ = os.RemoveAll(filepath.Join(dir, "hooks"))
	}
	if s.hasSubmodules() {
		modulesDir := filepath.Join(dotGitDir, "modules")
		walkRemove(modulesDir, "config", false)
		walkRemove(modulesDir, "hooks", false)
	}
}
```

**File:** shells/abstract.go (L1141-1170)
```go
// writeGitCleanupAllConfigs removes all git configs which are potentially open to malicious code injection:
// - the main git config & hooks
// - the template git config & hooks
// - any submodule's git config & hooks
// It's by default disabled for the shell executor or when the git strategy is "none", and enabled otherwise; explicit
// configuration however always has precedence.
func (b *AbstractShell) writeGitCleanupAllConfigs(sw ShellWriter, build *common.Build, cleanForSubmodules bool) {
	executor := build.Runner.Executor
	shouldCleanUp := (executor != "shell" && executor != "shell-integration-test" && build.GetGitStrategy() != common.GitNone)
	if config := build.Runner.CleanGitConfig; config != nil {
		shouldCleanUp = *config
	}
	if !shouldCleanUp {
		return
	}

	projectDir := build.FullProjectDir()

	// clean out configs in the main git dir and in the template dir
	for _, dir := range []string{sw.TmpFile(gitTemplateDir), sw.Join(projectDir, gitDir)} {
		sw.RmFile(sw.Join(dir, "config"))
		sw.RmDir(sw.Join(dir, "hooks"))
	}

	// clean out configs in the modules' git dirs
	if cleanForSubmodules {
		modulesDir := sw.Join(projectDir, gitDir, "modules")
		sw.RmFilesRecursive(modulesDir, "config")
		sw.RmDirsRecursive(modulesDir, "hooks")
	}
```

**File:** docs/configuration/advanced-configuration.md (L2473-2511)
```markdown
## Cleaning Git configuration

{{< history >}}

- [Introduced](https://gitlab.com/gitlab-org/gitlab-runner/-/merge_requests/5438) in GitLab Runner 17.10.

{{< /history >}}

At the beginning and end of every build, GitLab Runner removes the following
files from the repository and its submodules:

- Git lock files (`{index,shallow,HEAD,config}.lock`)
- Post-checkout hooks (`hooks/post-checkout`)

If you enable `clean_git_config`, the following additional files or directories
are removed from the repository, its submodules, and the Git template directory:

- `.git/config` file
- `.git/hooks` directory

This cleanup prevents custom, ephemeral, or potentially malicious Git configuration
from caching between jobs.

Before GitLab Runner 17.10, cleanups behaved differently:

- Git lock files and Post-checkout hooks cleanup only occurred at the
  beginning of a job and not at the end.
- Other Git configurations (now controlled by `clean_git_config`) were not removed unless
  `FF_ENABLE_JOB_CLEANUP` was set. When you set this flag, only the main repository's
  `.git/config` was deleted but not submodule configurations.

The `clean_git_config` setting defaults to `true`. But, it defaults to `false` when:

- [Shell executor](../executors/shell.md) is used.
- [Git strategy](https://docs.gitlab.com/ci/runners/configure_runners/#git-strategy)
  is set to `none`.

Explicit `clean_git_config` configuration takes precedence over the default
setting.
```
