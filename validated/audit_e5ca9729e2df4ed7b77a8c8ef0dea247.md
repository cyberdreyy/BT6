### Title
Symlink traversal in `GetSources.clearWorktree` allows host filesystem deletion outside the job workspace - (`functions/concrete/run/stages/get_sources.go`)

### Summary
`GetSources.clearWorktree` uses `os.Stat` and then runs `git rm -rf --ignore-unmatch .` and `git clean -ffdx` with `cmd.Dir = e.WorkingDir`. If `e.WorkingDir` (or a parent path component) is replaced by a symlink to a directory outside the job root before the retry fires, git follows the symlink and deletes files in the target directory. No symlink-aware validation exists before the destructive commands.

### Finding Description
- **Code path**: `GetSources.Run` loops over `MaxAttempts`; on `attempt == 2` with `ClearWorktreeOnRetry == true`, it calls `s.clearWorktree(ctx, e)` at `get_sources.go:156-160`. `clearWorktree` is defined at `get_sources.go:849-862`.
- **Root cause**: `clearWorktree` checks `os.Stat(e.WorkingDir)` and `info.IsDir()`, but `os.Stat` follows symlinks. If `e.WorkingDir` is a symlink, `info.IsDir()` returns true for the target, so the function proceeds. It then invokes `git(ctx, e, nil, "rm", "-rf", "--ignore-unmatch", ".")` and `git(ctx, e, nil, "clean", "-ffdx")`. `git` runs with `cmd.Dir = e.WorkingDir` (`env/env.go:145-164`). Git resolves `e.WorkingDir` as the symlink target and operates on that directory, deleting tracked/untracked files outside the intended workspace.
- **Attacker inputs**: A normal job can plant a symlink because prior stages run before `get_sources` in the same job environment. Specifically:
  - `CacheExtract.Run` extracts a cache archive via `cache-extractor` (`cache_extract.go:36-79`), and `ArtifactDownload.Run` downloads artifacts via `artifacts-downloader` (`artifact_download.go:25-58`). The extracted contents are under job control.
  - A job can also create symlinks in `before_script`, user scripts, or via artifact/cache contents.
  - The attacker can set `GET_SOURCES_ATTEMPTS >= 2` (a job variable, clamped to `[1,10]` by `builder.go:131`) to ensure the retry path is reached.
- **Exploit flow**:
  1. Job sets `GET_SOURCES_ATTEMPTS=2` (or higher) and uses `GIT_STRATEGY=fetch` (so `getSourcesOnce` does not `os.RemoveAll(e.WorkingDir)` before the first attempt).
  2. A prior stage (cache extract, artifact download, or user script) replaces `e.WorkingDir` with a symlink pointing to a sensitive host directory, e.g., `/home/gitlab-runner` or `/etc`.
  3. The first `getSourcesOnce` attempt fails (e.g., via a network-level or repo-level failure controllable by the attacker, or by making the repo unreachable).
  4. On `attempt == 2`, `clearWorktree` runs. `os.Stat` follows the symlink, sees a directory, and proceeds.
  5. `git rm -rf --ignore-unmatch .` and `git clean -ffdx` run with `cmd.Dir` set to the symlink path; git resolves the symlink and deletes files in the target directory.
- **Why checks fail**: There is no `os.Lstat` check, no symlink target validation, and no `safe.directory` or chroot-style confinement before the destructive git commands. `SafeDirectoryCheckout` only adds a `safe.directory` config entry and does not prevent symlink traversal. `cleanupGitState` also uses `filepath.Join(e.WorkingDir, ".git")` and is vulnerable to the same symlink issue, but it is not the focus here.

### Impact Explanation
Concrete scoped impact: deletion or modification of files on the runner host outside the job's workspace. If `e.WorkingDir` is symlinked to `/home/gitlab-runner`, `/var/lib/gitlab-runner`, `/tmp`, or another project's build directory, `git rm -rf` and `git clean -ffdx` can recursively delete files in that target directory. This violates the core invariant that file operations must stay within intended build/cache/artifact roots.

### Likelihood Explanation
- **Preconditions**:
  - `GET_SOURCES_ATTEMPTS >= 2` (job-controlled variable).
  - `ClearWorktreeOnRetry` is true (hardcoded to `true` in `builder.go:174` for this code path).
  - `GIT_STRATEGY` is `fetch` (so the first attempt does not remove and recreate `e.WorkingDir`).
  - The attacker can plant a symlink at `e.WorkingDir` before the retry fires, via cache/artifact extraction or prior job scripts.
  - The first `getSourcesOnce` attempt must fail to trigger the retry.
- **Feasibility**: High for an attacker controlling CI config and job inputs. `GET_SOURCES_ATTEMPTS` is a documented job variable. Planting symlinks via cache/artifact archives or scripts is standard. Triggering a git fetch failure is achievable (e.g., invalid ref, unreachable repo, or repo-level misconfiguration).
- **Repeatability**: Fully repeatable; the retry loop is deterministic once the preconditions are met.

### Recommendation
Before running destructive git commands in `clearWorktree`, validate `e.WorkingDir` with `os.Lstat` and reject symlinks, or resolve and verify the path is still within the job root. For example:
- Use `os.Lstat(e.WorkingDir)`; if the result is a symlink, return an error or remove the symlink without following it.
- Alternatively, open the job root directory with `os.Open` and use `/proc/self/fd`-based or `O_NOFOLLOW`-style path traversal to ensure git operates on the real workspace directory, not a symlink.

### Proof of Concept
Go unit test plan for `functions/concrete/run/stages/get_sources_test.go`:

```go
func TestGetSources_ClearWorktree_DoesNotFollowSymlink(t *testing.T) {
	e := newTestEnv(t, "bash")

	// Create a sentinel directory outside the intended workspace.
	sentinel := t.TempDir()
	sentinelFile := filepath.Join(sentinel, "must-survive")
	require.NoError(t, os.WriteFile(sentinelFile, []byte("sentinel"), 0o644))

	// Replace WorkingDir with a symlink pointing outside the workspace.
	require.NoError(t, os.RemoveAll(e.WorkingDir))
	require.NoError(t, os.Symlink(sentinel, e.WorkingDir))

	// Initialize a git repo in the sentinel dir so git rm/clean have something to do.
	require.NoError(t, exec.CommandContext(t.Context(), "git", "-C", sentinel, "init").Run())
	require.NoError(t, os.WriteFile(filepath.Join(sentinel, "tracked"), []byte("tracked"), 0o644))
	require.NoError(t, exec.CommandContext(t.Context(), "git", "-C", sentinel, "add", ".").Run())

	gs := GetSources{}
	err := gs.clearWorktree(t.Context(), e)

	// The current implementation will succeed and delete sentinel contents.
	// A fixed implementation should either error or leave sentinelFile intact.
	require.NoError(t, err)
	require.FileExists(t, sentinelFile, "clearWorktree must not delete files outside the job workspace")
}
```

Expected behavior before fix: `sentinelFile` is deleted (test fails). After fix: `sentinelFile` survives (test passes).