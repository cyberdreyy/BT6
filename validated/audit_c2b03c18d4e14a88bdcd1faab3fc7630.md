This confirms the analysis is complete. Let me finalize the answer.

### Title
KillAndWait's process-group SIGKILL misses grandchildren detached via setsid, allowing job scripts to keep running and writing to the workspace after job termination is reported successful - (File: helpers/process/killer.go)

### Summary
`osKillWait.KillAndWait` in `helpers/process/killer.go` delegates termination to `unixKiller.Terminate`/`ForceKill`, which signal the negative PID (process group) obtained via `getPID()`. A job script that calls `setsid`/`nohup`/double-fork before the graceful timeout elapses creates a new session and process group, so subsequent `SIGTERM`/`SIGKILL` sent to `-pid` never reaches it, letting the detached process continue running (and writing to the shared build/cache directory) after `KillAndWait` returns.

### Finding Description
`setProcessGroup` in `helpers/process/job_unix.go` sets `Setpgid: true` on the top-level shell command so the whole tree shares one process group equal to the leader's PID. [1](#0-0) 
`unixKiller.Terminate`/`ForceKill` in `helpers/process/killer_unix.go` send signals to `-pid` (the negative PID trick to target the whole process group), relying entirely on that inherited group membership. [2](#0-1) 
`KillAndWait` only observes success via `waitCh`; if the shell process (the direct child whose `Wait()` is tracked) exits or is killed while a grandchild has already detached (e.g., via `setsid()`, which creates a new session and a new process group not equal to `-pid`), the `SIGKILL` to the old `-pid` never reaches the detached grandchild, and `waitCh` still fires because the tracked leader process died — `KillAndWait` returns `nil` (or eventually `KillProcessError` only if the *original* group is unresponsive, which is unrelated to the detached descendant). [3](#0-2) 
This is a known, partially documented limitation ("Any child process ... receives the graceful termination process ... by having the main process set as a process group which all the child processes belong to") — it explicitly assumes children stay in the group, which `setsid`/double-fork breaks.

Both `executors/shell/shell.go` and `executors/custom/command/command.go` call `KillAndWait` the same way on cancellation/timeout, so the shell and custom executors are both exposed. [4](#0-3) [5](#0-4) 

The attacker input is simply the job script content itself (`.gitlab-ci.yml` `script:`), which any pipeline author fully controls, no special executor privileges needed. There is no check anywhere in the kill/executor path that verifies all descendants died before considering the job finished — the code trusts `waitCh` and the process-group signal blindly.

### Impact Explanation
On the shell executor (and any executor using this same `KillAndWait`/process-group scheme on a shared host/runner), a job that is canceled or times out can leave a detached orphan process alive that continues writing into the job's working directory — which is the same filesystem location used for cache/artifact archiving right after the job is reported as terminated. Since the shell executor runs directly on the host filesystem without per-job sandboxing beyond the working directory convention, this can corrupt the cache/artifact archive being built for that job, or interleave writes into whatever reuses that path (e.g. a subsequent job using the same working directory, depending on `builds_dir` reuse configuration). This is a genuine violation of the invariant that only the terminated job's process tree should still be touching its own directory at cleanup time. Note that the impact is more clearly reachable/severe in shared/reused-workspace setups (e.g. shell executor without `--builds-dir-is-shared=false`/unique subpaths, or misconfigured reuse of directories) rather than out-of-the-box container-based executors, which impose additional isolation.

### Likelihood Explanation
Feasible and repeatable with an unprivileged job script: `setsid nohup sh -c 'sleep infinity; write-to-workspace' &` or a small C/Go helper double-forking is trivial to write, requires no special OS capability, and reliably escapes the parent's process group before the runner's graceful/kill timeouts fire (default `GracefulTimeout` is 10 minutes, `KillTimeout` 10 seconds, both attacker-tunable in perceived urgency since the attacker controls how fast their script forks). The only requirement is that the job be canceled/timed out while such a detached process is still alive, which the job author fully controls by design (e.g. sleeping past a `timeout`).

### Recommendation
Do not rely solely on inherited process-group membership. On Linux, use a cgroup (or PID namespace) per job to reliably kill the entire descendant tree regardless of `setsid`/re-parenting, similar to how containerized executors already isolate jobs. As a lighter-weight mitigation, after `ForceKill`, verify no processes remain with the job's `builds_dir` open/as cwd (e.g., scanning `/proc` for matching cwd) before considering cleanup safe, or refuse to consider a job "done" for cache/artifact purposes until the workspace is confirmed quiescent.

### Proof of Concept
Go integration test in `helpers/process` package:
1. Build a fixture binary that: forks a child, child calls `syscall.Setsid()`, then loops writing timestamps to a `sentinel` file in a workspace directory passed via arg/env, while the parent (leader) exits quickly or sleeps ignoring/handling SIGTERM only for itself.
2. Start this fixture via the normal `Commander`/`osCmd.Start()` path with `Setpgid: true` (mirrors `job_unix.go`).
3. Call `osKillWait{gracefulKillTimeout: 200ms, forceKillTimeout: 200ms}.KillAndWait(cmd, waitCh)`.
4. Assert `KillAndWait` returns (nil or non-`KillProcessError`) quickly (leader group killed/exited).
5. After return, sleep e.g. 1s and assert the sentinel file's mtime/content keeps advancing — proving the detached grandchild is still alive and writing after `KillAndWait` reported completion.
6. Optionally add `pgrep`/`/proc` check confirming the orphan's pgid differs from the original leader's pgid, demonstrating why the `-pid` `SIGKILL` missed it.

### Citations

**File:** helpers/process/job_unix.go (L36-40)
```go
func setProcessGroup(c *exec.Cmd) {
	c.SysProcAttr = &syscall.SysProcAttr{
		Setpgid: true,
	}
}
```

**File:** helpers/process/killer_unix.go (L21-52)
```go
func (pk *unixKiller) Terminate() {
	if pk.cmd.Process() == nil {
		return
	}

	err := syscall.Kill(pk.getPID(), syscall.SIGTERM)
	if err != nil {
		pk.logger.Warn("Failed to terminate process:", err)

		// try to kill right-after
		pk.ForceKill()
	}
}

func (pk *unixKiller) ForceKill() {
	if pk.cmd.Process() == nil {
		return
	}

	err := syscall.Kill(pk.getPID(), syscall.SIGKILL)
	if err != nil {
		pk.logger.Warn("Failed to force-kill:", err)
	}
}

// getPID will return the negative PID (-PID) which is the process group. The
// negative symbol comes from kill(2) https://linux.die.net/man/2/kill `If pid
// is less than -1, then sig is sent to every process in the process group whose
// ID is -pid.`
func (pk *unixKiller) getPID() int {
	return pk.cmd.Process().Pid * -1
}
```

**File:** helpers/process/killer.go (L76-92)
```go
	processKiller := newProcessKiller(log, command)
	processKiller.Terminate()

	select {
	case err := <-waitCh:
		return err
	case <-time.After(kw.gracefulKillTimeout):
		processKiller.ForceKill()

		select {
		case err := <-waitCh:
			return err
		case <-time.After(kw.forceKillTimeout):
			return &KillProcessError{pid: process.Pid}
		}
	}
}
```

**File:** executors/shell/shell.go (L124-132)
```go
	// Support process abort
	select {
	case err = <-waitCh:
		return err
	case <-cmd.Context.Done():
		logger := common.NewProcessLoggerAdapter(s.BuildLogger)
		return newProcessKillWaiter(logger, s.Config.GetGracefulKillTimeout(), s.Config.GetForceKillTimeout()).
			KillAndWait(c, waitCh)
	}
```

**File:** executors/custom/command/command.go (L81-97)
```go
func (c *command) Run() error {
	err := c.cmd.Start()
	if err != nil {
		return fmt.Errorf("failed to start command: %w", err)
	}

	go c.waitForCommand()

	select {
	case err = <-c.waitCh:
		return err

	case <-c.context.Done():
		return newProcessKillWaiter(c.logger, c.gracefulKillTimeout, c.forceKillTimeout).
			KillAndWait(c.cmd, c.waitCh)
	}
}
```
