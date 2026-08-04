### Title
Cleanup silently ignores permission-denied removal errors, allowing planted `.git/hooks/post-checkout` to persist and execute in the next job on a reused workspace - (File: functions/concrete/run/stages/cleanup.go)

### Summary
`Cleanup.Run` unconditionally returns `nil` and both `cleanBuildDirectory`/`cleanGitState` discard every `os.*`/`git()` error via `_ =`, so the documented post-checkout-hook and git-config mitigation is only "best effort." A job that chmods `.git/hooks` to remove write permission for the runner's OS user can make the hook-removal `os.Remove` calls fail silently in both `Cleanup.Run` (end of job A) and `GetSources.cleanupGitState` (start of job B), letting a malicious `post-checkout` hook survive into the next job that reuses the same workspace.

### Finding Description
`cleanGitState` (functions/concrete/run/stages/cleanup.go:64-99) removes `.git/hooks/post-checkout` and (with `clean_git_config`) `.git/hooks`/`.git/config` via `os.Remove`/`os.RemoveAll`, but every call is prefixed with `_ =`, and `Cleanup.Run` (lines 20-28) returns `nil` regardless of outcome. The same discard pattern exists in `GetSources.cleanupGitState` (functions/concrete/run/stages/get_sources.go:557-592), which repeats the same removal at the *start* of the next job before `gitInit`/`gitClone`/`gitCheckout` run.

On POSIX, `unlink()` on a file requires write+execute permission on the *containing directory*, not the file itself. A job script (running with normal job permissions, e.g. shell executor same-uid, or any executor with a workspace that persists across jobs via `GIT_STRATEGY=fetch`) can:
1. Write `.git/hooks/post-checkout` with malicious content and make it executable.
2. `chmod 555 .git/hooks` (or similar) to strip write permission from the hooks directory for the runner process's user, before the job's cleanup stage runs.

Because job A owns/created that directory, it can freely restrict permissions on its own workspace. When `Cleanup.Run` executes `os.Remove(filepath.Join(dotGitDir, "hooks", "post-checkout"))` (cleanup.go:72), the call fails with `EACCES`/`EPERM` but the error is discarded. When job B starts, `GetSources.Run` calls `s.cleanupGitState(e)` (get_sources.go:146) which performs the identical removal and again silently fails. `gitInit`'s `git init --template templateDir` (get_sources.go:594-623) does not overwrite pre-existing files in `.git/hooks`, so the planted hook is preserved. `gitCheckout`'s `git checkout -f -q SHA` (get_sources.go:678-697) then executes the surviving `post-checkout` hook in job B's context.

Existing checks are insufficient: the hook-removal logic exists precisely to close this known cross-job hook-persistence vector (documented in `docs/configuration/advanced-configuration.md` "Cleaning Git configuration", introduced in GitLab Runner 17.10 / MR !5438), but its enforcement is not verified — a failed removal produces no error, no warning, and does not block job start or fail the job.

### Impact Explanation
A job (e.g. running on an untrusted/unprotected branch, MR pipeline, or any pipeline sharing a runner workspace keyed by project/concurrent slot) can plant an executable git hook that survives cleanup and later executes arbitrary code inside a subsequent job on the same workspace — including a protected-branch job with elevated `CI_JOB_TOKEN`/deploy credentials/secrets. This is code execution with the later job's authorization context, i.e. secret exfiltration or lateral pipeline compromise, matching the "secrets/tokens must not leak across jobs" and "job auth state must not let one job impersonate another" invariants.

### Likelihood Explanation
Requires: (a) a workspace/executor that reuses the same build directory across jobs (default for `GIT_STRATEGY=fetch`, and always true for the shell executor and many persistent-volume setups), and (b) the job process having enough filesystem permission over its own `.git/hooks` directory to chmod it (true in shell executor and most container executors where the job runs as the workspace owner). No admin misconfiguration beyond standard, common runner setups is required, and the attack is fully repeatable/deterministic since the removal logic never re-checks or retries.

### Recommendation
Make hook/config cleanup fail loudly instead of best-effort:
- Check and propagate errors from `os.Remove`/`os.RemoveAll` in `cleanGitState` and `GetSources.cleanupGitState`, distinguishing "not found" (ignorable) from permission/other errors (fatal).
- If a stale hook file cannot be removed, fail the job (or at minimum force `git init`/`git clone` to use a location outside the attacker-writable path, e.g. always `os.RemoveAll` the whole `.git` directory rather than `.git/hooks/post-checkout` alone when `CleanGitConfig` or hook removal fails) rather than proceeding to checkout.
- Alternatively/defensively, disable hook execution entirely during Runner-driven checkouts via `-c core.hooksPath=<empty-writable-dir>` or `GIT_CONFIG_NOSYSTEM`-style isolation for the `git checkout`/`git clone`/`git fetch` invocations in `get_sources.go`, so leftover hooks can never execute regardless of cleanup success.

### Proof of Concept
Go integration test sketch (extends `get_sources_git_integration_test.go` style):
```go
func TestCrossJobHookPersistsWhenCleanupBlocked(t *testing.T) {
    // Job A: clone/fetch, then simulate malicious script:
    // 1. write executable .git/hooks/post-checkout that touches a marker file
    // 2. os.Chmod(filepath.Join(workDir, ".git", "hooks"), 0o555)
    // Run Cleanup{GitStrategy: "fetch", CleanGitConfig: true}.Run(ctx, env)
    // assert marker hook file still exists (removal silently failed)

    // restore chmod is NOT called (simulating attacker leaving it protected)

    // Job B: GetSources{GitStrategy: "fetch", Checkout: true, ...}.Run(ctx, env)
    // assert marker file created by hook does NOT exist
    // (currently FAILS: hook executes because cleanupGitState's os.Remove
    // also failed silently and git checkout ran the hook)
}
```
Expected assertion: job B's checkout must not execute the stale hook; test should fail against current code (marker file present after job B), demonstrating the bypass. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** functions/concrete/run/stages/cleanup.go (L20-28)
```go
func (s Cleanup) Run(ctx context.Context, e *env.Env) error {
	if s.EnableJobCleanup {
		s.cleanBuildDirectory(ctx, e)
	}

	s.cleanGitState(e)

	return nil
}
```

**File:** functions/concrete/run/stages/cleanup.go (L64-92)
```go
func (s Cleanup) cleanGitState(e *env.Env) {
	projectDir := e.WorkingDir
	dotGitDir := filepath.Join(projectDir, ".git")

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
		walkRemove(modulesDir, "post-checkout", false)
	}

	walkRemove(filepath.Join(dotGitDir, "refs"), ".lock", true)

	if !s.CleanGitConfig {
		return
	}

	tmpDir := e.WorkingDir + ".tmp"
	for _, dir := range []string{filepath.Join(tmpDir, templateDirName), dotGitDir} {
		_ = os.Remove(filepath.Join(dir, "config"))
		_ = os.RemoveAll(filepath.Join(dir, "hooks"))
	}
```

**File:** functions/concrete/run/stages/get_sources.go (L146-146)
```go
	s.cleanupGitState(e)
```

**File:** functions/concrete/run/stages/get_sources.go (L557-586)
```go
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
```

**File:** functions/concrete/run/stages/get_sources.go (L594-623)
```go
func (s GetSources) gitInit(ctx context.Context, e *env.Env, templateDir, remoteURL, extConfigFile string, extraEnv map[string]string) error {
	args := []string{"init", ".", "--template", templateDir}
	if s.ObjectFormat != "" && s.ObjectFormat != "sha1" {
		args = append(args, "--object-format", s.ObjectFormat)
	}

	if err := git(ctx, e, extraEnv, args...); err != nil {
		return fmt.Errorf("git init: %w", err)
	}

	if err := git(ctx, e, extraEnv, "remote", "add", "origin", remoteURL); err != nil {
		if err := git(ctx, e, extraEnv, "remote", "set-url", "origin", remoteURL); err != nil {
			return fmt.Errorf("setting remote URL: %w", err)
		}
		// For existing repos the template isn't reapplied — explicitly include
		// the external config.
		absExtConfig, _ := filepath.Abs(extConfigFile)
		pattern := regexp.QuoteMeta(filepath.Base(extConfigFile)) + "$"
		if err := git(ctx, e, extraEnv,
			"config", "--file", filepath.Join(e.WorkingDir, ".git", "config"),
			"--replace-all", "include.path", absExtConfig, pattern,
		); err != nil {
			e.Warningf("Failed to configure include.path for existing repo: %v", err)
		}
	} else {
		e.Noticef("Created fresh repository.")
	}

	return nil
}
```

**File:** functions/concrete/run/stages/get_sources.go (L678-697)
```go
func (s GetSources) gitCheckout(ctx context.Context, e *env.Env, extraEnv map[string]string) error {
	short := s.SHA
	if len(short) > 8 {
		short = short[:8]
	}
	e.Noticef("Checking out %s as detached HEAD (ref is %s)...", short, s.Ref)

	checkoutArgs := append(s.configArgs(), "-c", "submodule.recurse=false", "checkout", "-f", "-q", s.SHA)
	if err := git(ctx, e, extraEnv, checkoutArgs...); err != nil {
		return fmt.Errorf("git checkout: %w", err)
	}

	if len(s.GitCleanFlags) > 0 {
		if err := git(ctx, e, extraEnv, append([]string{"clean"}, s.GitCleanFlags...)...); err != nil {
			return fmt.Errorf("git clean: %w", err)
		}
	}

	return nil
}
```

**File:** docs/configuration/advanced-configuration.md (L2481-2494)
```markdown
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
```
