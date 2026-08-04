### Title
Missing symlink validation on `e.WorkingDir` allows a prior GIT_STRATEGY=fetch job to redirect all subsequent git/archiver subprocesses outside the build root - ([File: functions/concrete/run/env/env.go])

### Summary
`env.Env.Command` sets `cmd.Dir = e.WorkingDir` with no check that the path is still a real directory rather than a symlink, and `GetSources` (fetch strategy) and `Cleanup` (fetch strategy) never recreate `WorkingDir` between jobs. A job that replaces its own project directory with a symlink before finishing therefore permanently redirects every subsequent git command (and any other `e.Command` invocation) of a later job sharing that build path to an attacker-chosen filesystem location.

### Finding Description
`GIT_STRATEGY` is a user-controlled CI/CD variable, validated only against the allowed enum values `empty|none|fetch|clone` in `buildGetSources` [1](#0-0) .

In `GetSources.Run`/`getSourcesOnce`, only `clone` and `empty` strategies call `os.RemoveAll`+`os.MkdirAll` on `e.WorkingDir` before use; the `fetch` strategy path skips straight into `setupGlobalGitConfig`/`gitInit`/`gitFetch`/`gitCheckout` operating on the existing `e.WorkingDir` as-is: [2](#0-1) [3](#0-2) .

`Cleanup.cleanBuildDirectory` for the `fetch` strategy likewise never removes/recreates `projectDir` (`e.WorkingDir`); it only runs `git clean`/`git reset --hard` through it: [4](#0-3) .

Every one of those git invocations, and any other helper/archiver subprocess, ultimately goes through `env.Env.Command`, which blindly assigns `cmd.Dir = e.WorkingDir` with no `os.Lstat`/`filepath.EvalSymlinks` check: [5](#0-4) . No code path anywhere under `functions/concrete/run` performs a symlink check on `WorkingDir` (confirmed by searching for `Lstat`/`ModeSymlink`/`EvalSymlinks` in that tree — the only hit is unrelated, `resolveBundle`'s executable-path resolution) [6](#0-5) .

The build path itself is derived deterministically per runner/concurrency-slot/project and is intentionally *reused* across consecutive builds of the same project so that `GIT_STRATEGY=fetch` can incrementally reuse the existing clone — this is the documented behavior (`{builds_dir}/$RUNNER_TOKEN_KEY/$CONCURRENT_PROJECT_ID/$NAMESPACE/$PROJECT_NAME`, and for Kubernetes/Docker the explicitly-recommended "persistent per-concurrency build volumes" feature) [7](#0-6) .

Exploit flow:
1. Attacker (any pipeline author with push access to a project, e.g. an MR pipeline) sets `GIT_STRATEGY=fetch` (default when `AllowGitFetch` is on) and runs a job whose `script:` (fully user-controlled) does, at the end: `rm -rf "$CI_PROJECT_DIR" && ln -s /some/target "$CI_PROJECT_DIR"`.
2. `Cleanup.Run` for `fetch` strategy does not detect or repair this; it just runs `git reset --hard` etc. through the now-symlinked path and silently fails/no-ops.
3. A later job for the *same project* on the *same runner concurrency slot* (a subsequent commit, a scheduled pipeline, a protected-branch pipeline run by a different, more privileged user/secret set, or the attacker's own next job) is scheduled onto the same build path. `GetSources.Run` with `GIT_STRATEGY=fetch` does not `RemoveAll`/recreate `WorkingDir`, so `gitInit`/`gitFetch`/`gitCheckout` execute with `cmd.Dir` resolving through the symlink to `/some/target`, writing/overwriting arbitrary files there.
4. No existing check (`SafeDirectoryCheckout`/`configureSafeDirectory` only adds a `git config safe.directory` entry for ownership mismatches, not symlink detection) stops this [8](#0-7) .

### Impact Explanation
A job that plants a symlink at its own build path causes every later job that reuses that same build path (a different, possibly more-trusted pipeline run of the same project — e.g. a protected-branch deploy job with elevated CI/CD secrets, or the same project's next scheduled pipeline) to execute all git/helper subprocesses with their working directory resolved to an attacker-chosen location. This can overwrite or expose arbitrary files reachable at that location (subject to host filesystem permissions of the runner/job user), violating the invariant that file operations stay within the intended build root and that one job's workspace stays isolated from another's.

### Likelihood Explanation
Preconditions are realistic and fully attacker-reachable without any admin/cluster compromise: `GIT_STRATEGY=fetch` is a normal, commonly-used, user-settable CI variable; the build path is deterministically reused across builds of the same project/concurrency-slot by design (persistent build volumes are the officially documented way to make `fetch` useful on Kubernetes/Docker, and always true for shell/ssh/instance executors); and creating a symlink inside one's own already-writable build directory requires no special privilege — it's ordinary shell script content in the attacker's own job. The only additional requirement is that a second job for the same project subsequently reuses that same path, which happens naturally on any project with repeated pipeline runs.

### Recommendation
Before using `e.WorkingDir` in `GetSources`/`Cleanup`/`env.Env.Command`, verify with `os.Lstat` that the path, if it exists, is a real directory and not a symlink (and ideally that it resolves, via `filepath.EvalSymlinks`, to a location under the configured builds root). If it is a symlink, either refuse the job with a clear error or force a fetch-strategy `RemoveAll`+`MkdirAll` (same treatment as `clone`/`empty`) instead of operating through it.

### Proof of Concept
Go integration test added to `functions/concrete/run/stages/get_sources_git_integration_test.go`:
```go
func TestGetSourcesGit_Fetch_RefusesSymlinkedWorkingDir(t *testing.T) {
    repoURL, sha, ref := testRepo(t)
    e := gitEnv(t, "bash")

    outsideDir := t.TempDir() // simulates a location outside the builds root
    require.NoError(t, os.RemoveAll(e.WorkingDir))
    require.NoError(t, os.Symlink(outsideDir, e.WorkingDir))

    gs := stages.GetSources{
        GitStrategy: "fetch",
        Checkout:    true,
        RepoURL:     repoURL,
        SHA:         sha,
        Ref:         ref,
        Refspecs:    []string{"+refs/heads/*:refs/remotes/origin/*"},
        MaxAttempts: 1,
    }

    err := gs.Run(context.Background(), e)

    // Expected (after fix): error rejecting symlinked WorkingDir
    // Current (unfixed): succeeds, git operations execute with cwd resolved through symlink
    require.Error(t, err, "should reject symlinked WorkingDir")
    assert.Contains(t, err.Error(), "symlink", "error should mention symlink")
}
``` [9](#0-8)

### Citations

**File:** functions/concrete/builder/builder.go (L140-140)
```go
		GitStrategy:                     variables.Default(b.variables, "GIT_STRATEGY", defaultGitStrategy, "empty", "none", "fetch", "clone"),
```

**File:** functions/concrete/run/stages/get_sources.go (L108-126)
```go
func (s GetSources) Run(ctx context.Context, e *env.Env) error {
	switch s.GitStrategy {
	case gitStrategyNone:
		e.Noticef("Skipping Git repository setup")
		return os.MkdirAll(e.WorkingDir, 0o755)

	case gitStrategyEmpty:
		e.Noticef("Skipping Git repository setup and creating an empty build directory")
		if err := os.RemoveAll(e.WorkingDir); err != nil {
			return fmt.Errorf("removing project dir: %w", err)
		}
		return os.MkdirAll(e.WorkingDir, 0o755)

	case gitStrategyFetch, gitStrategyClone:
		// handled below

	default:
		return fmt.Errorf("unknown GIT_STRATEGY: %s", s.GitStrategy)
	}
```

**File:** functions/concrete/run/stages/get_sources.go (L176-217)
```go
func (s GetSources) getSourcesOnce(ctx context.Context, e *env.Env, gitEnv map[string]string) error {
	if s.GitStrategy == gitStrategyClone {
		if err := os.RemoveAll(e.WorkingDir); err != nil {
			return fmt.Errorf("removing project dir for clone: %w", err)
		}
		if err := os.MkdirAll(e.WorkingDir, 0o755); err != nil {
			return fmt.Errorf("recreating project dir: %w", err)
		}
	}

	globalCleanup, err := s.setupGlobalGitConfig(ctx, e, gitEnv)
	if err != nil {
		return err
	}
	defer globalCleanup()

	extConfigFile, cleanupConfig, err := s.setupExternalGitConfig(ctx, e, gitEnv)
	if err != nil {
		return fmt.Errorf("setting up git config: %w", err)
	}
	defer cleanupConfig()

	templateDir, cleanupTemplate, err := s.setupTemplateDir(e, extConfigFile)
	if err != nil {
		return fmt.Errorf("setting up template dir: %w", err)
	}
	defer cleanupTemplate()

	remoteURL := s.remoteURLWithoutCreds()

	if s.GitStrategy == gitStrategyClone && s.UseNativeClone && gitVersionAtLeast(ctx, e, gitMinVersionCloneWithRef) {
		if err := s.gitClone(ctx, e, templateDir, remoteURL, gitEnv); err != nil {
			return err
		}
	} else {
		if err := s.gitInit(ctx, e, templateDir, remoteURL, extConfigFile, gitEnv); err != nil {
			return err
		}
		if err := s.gitFetch(ctx, e, gitEnv); err != nil {
			return err
		}
	}
```

**File:** functions/concrete/run/stages/get_sources.go (L279-291)
```go
// configureSafeDirectory adds a safe.directory entry to whichever global
// config gitEnv["GIT_CONFIG_GLOBAL"] points at. safe.directory must be
// set at global scope; git ignores it at repo level. No-ops when
// SafeDirectoryCheckout is unset.
func (s GetSources) configureSafeDirectory(ctx context.Context, e *env.Env, gitEnv map[string]string) error {
	if !s.SafeDirectoryCheckout {
		return nil
	}
	if err := git(ctx, e, gitEnv, "config", "--global", "--add", "safe.directory", e.WorkingDir); err != nil {
		return fmt.Errorf("adding safe.directory: %w", err)
	}
	return nil
}
```

**File:** functions/concrete/run/stages/cleanup.go (L30-42)
```go
func (s Cleanup) cleanBuildDirectory(ctx context.Context, e *env.Env) {
	projectDir := e.WorkingDir

	switch s.GitStrategy {
	case gitStrategyClone, gitStrategyEmpty:
		_ = os.RemoveAll(projectDir)

	case gitStrategyFetch:
		if len(s.GitCleanFlags) > 0 {
			_ = git(ctx, e, nil, append([]string{"clean"}, s.GitCleanFlags...)...)
		}

		_ = git(ctx, e, nil, "reset", "--hard")
```

**File:** functions/concrete/run/env/env.go (L145-164)
```go
func (e *Env) Command(ctx context.Context, name string, env map[string]string, args ...string) error {
	environ := os.Environ()
	for k, v := range e.Env {
		environ = append(environ, k+"="+v)
	}
	for k, v := range e.GitLabEnv {
		environ = append(environ, k+"="+v)
	}
	for k, v := range env {
		environ = append(environ, k+"="+v)
	}

	cmd := gracefulexitcmd.New(ctx, e.GracefulExitDelay, name, args...)
	cmd.Dir = e.WorkingDir
	cmd.Env = environ
	cmd.Stdout = e.Stdout
	cmd.Stderr = e.Stderr

	return normalizeExitError(cmd.Run(), cmd.ProcessState)
}
```

**File:** functions/concrete/run/env/env.go (L258-279)
```go
func (e *Env) resolveBundle() {
	e.resolveBundleOnce.Do(func() {
		e.bundledGit = "git"

		exe, err := os.Executable()
		if err != nil {
			return
		}

		exe, _ = filepath.EvalSymlinks(exe)
		baseDir := filepath.Dir(exe)

		candidate := filepath.Join(baseDir, "git", "bin", "git")
		if _, err := os.Stat(candidate); err == nil {
			e.bundledGit = candidate
		}

		candidate = filepath.Join(baseDir, "ca-certs.pem")
		if _, err := os.Stat(candidate); err == nil {
			e.bundledCACerts = candidate
		}
	})
```

**File:** docs/configuration/advanced-configuration.md (L2459-2462)
```markdown
GitLab Runner uses the _Builds Directory_ for all the jobs that it
runs, but nests them using a specific pattern
`{builds_dir}/$RUNNER_TOKEN_KEY/$CONCURRENT_PROJECT_ID/$NAMESPACE/$PROJECT_NAME`.
For example: `/builds/2mn-ncv-/0/user/playground`.
```

**File:** functions/concrete/run/stages/get_sources_git_integration_test.go (L233-310)
```go
func TestGetSourcesGit_Fetch(t *testing.T) {
	tests := map[string]struct {
		depth         int
		checkout      bool
		runTwice      bool
		gitFetchFlags []string
		expectShallow bool
		expectFile    bool
	}{
		"basic": {
			checkout:   true,
			expectFile: true,
		},
		"idempotent (run twice)": {
			checkout:   true,
			runTwice:   true,
			expectFile: true,
		},
		"with depth": {
			depth:         1,
			checkout:      true,
			expectShallow: true,
			expectFile:    true,
		},
		"no checkout": {
			checkout:   false,
			expectFile: false,
		},
		"with extra fetch flags": {
			checkout:      true,
			gitFetchFlags: []string{"--no-tags"},
			expectFile:    true,
		},
	}

	for name, tc := range tests {
		t.Run(name, func(t *testing.T) {
			repoURL, sha, ref := testRepo(t)
			e := gitEnv(t, "bash")

			gs := stages.GetSources{
				GitStrategy:   "fetch",
				Checkout:      tc.checkout,
				Depth:         tc.depth,
				RepoURL:       repoURL,
				SHA:           sha,
				Ref:           ref,
				Refspecs:      []string{"+refs/heads/*:refs/remotes/origin/*"},
				GitFetchFlags: tc.gitFetchFlags,
				MaxAttempts:   1,
			}

			err := gs.Run(context.Background(), e)
			require.NoError(t, err, "stderr: %s", e.Stderr.(*bytes.Buffer).String())

			if tc.runTwice {
				err = gs.Run(context.Background(), e)
				require.NoError(t, err, "stderr: %s", e.Stderr.(*bytes.Buffer).String())
			}

			if tc.expectFile {
				assert.FileExists(t, filepath.Join(e.WorkingDir, "hello.txt"))
			} else {
				assert.DirExists(t, filepath.Join(e.WorkingDir, ".git"))
				assert.NoFileExists(t, filepath.Join(e.WorkingDir, "hello.txt"))
			}

			if tc.checkout {
				actual := runOutput(t, e.WorkingDir, "git", "rev-parse", "HEAD")
				assert.Equal(t, sha, actual)
			}

			if tc.expectShallow {
				assert.FileExists(t, filepath.Join(e.WorkingDir, ".git", "shallow"))
			}
		})
	}
}
```
