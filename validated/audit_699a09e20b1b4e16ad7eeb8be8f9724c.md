### Title
Backgrounded/detached (double-forked or `setsid`) child processes escape process-group signal kill on job cancellation - ([File: executors/custom/command/command.go])

### Summary
The custom executor's `KillAndWait` mechanism relies exclusively on POSIX process-group signaling (`kill(-pgid, SIGTERM/SIGKILL)`) to terminate a job's process tree on cancellation. Any child process that detaches from that process group (e.g., via `setsid`, double-fork, `nohup … & disown`) is not a member of the signaled group and survives job cancellation, violating the invariant that cancellation fully terminates job-spawned processes.

### Finding Description
`command.Run()` starts the custom-executor driver process with `newCommander`, which on Unix (`helpers/process/job_unix.go`) sets `SysProcAttr.Setpgid = true` before `Start()`: [1](#0-0) [2](#0-1) 

This makes the launched process the leader of a new process group, whose ID equals its PID. When `ctx.Done()` fires, `Run()` calls `newProcessKillWaiter(...).KillAndWait(c.cmd, c.waitCh)`: [3](#0-2) 

`KillAndWait` in turn calls `processKiller.Terminate()` / `ForceKill()`, both implemented in `unixKiller` using `syscall.Kill(pk.getPID(), SIGTERM/SIGKILL)`, where `getPID()` returns the *negative* PID (i.e., targets the whole process group): [4](#0-3) 

A negative-PID `kill()` only delivers the signal to processes that are still members of that specific process group. Any child spawned by the job script that calls `setsid()` (directly, or implicitly via shell constructs like `nohup cmd &`, `setsid cmd &`, or a double-fork daemonizing pattern) creates a new session and a new process group with a different PGID. Such a process is no longer reachable by the group-targeted `SIGTERM`/`SIGKILL` sent by `unixKiller`. The runner has no fallback mechanism (no cgroup-based kill, no PID namespace, no recursive `/proc` child-tree scan) to catch processes that have left the group — confirmed by the absence of any cgroup/PID-namespace enforcement in this reap path (only Windows has an analogous `EnsureSubprocessTerminationOnExit`/job-object mechanism, and even that is a no-op on Unix): [5](#0-4) 

Consequently, after `KillAndWait` returns (successfully reaping only the direct driver process via `waitCh`), the detached grandchild process keeps running on the host/VM past job cancellation, with continued access to the shared workspace directory, environment, and (in shell/custom-executor-with-shared-host configurations) potentially still-mounted session/workspace paths that will be reused by the next job.

### Impact Explanation
A job-controlled script that detaches a child process (e.g., `setsid sleep 3600 &` or a small daemonizing helper) survives explicit job cancellation/timeout. If the executor reuses the same workspace or host filesystem for subsequent jobs (a supported and common custom/shell executor configuration), the surviving process can continue to read/write files in the job workspace after the job is marked cancelled, and can still be present when the next job's workspace is provisioned in the same directory, enabling cross-job interference/data exposure. This is a concrete violation of "job cancellation fully terminates all job-spawned processes."

### Likelihood Explanation
This is trivially reachable by any pipeline author with control over the job script executed by `RunExec` (or by shell executor scripts too, since `shell.go` uses the same `newProcessKillWaiter`/`KillAndWait` pattern). No special privileges are needed beyond normal CI job authoring — a single `setsid <cmd> &` line, or a two-line double-fork trick, is sufficient. The behavior is deterministic and repeatable across POSIX systems on which `Setpgid`-based killing is the only termination mechanism.

### Recommendation
- Do not rely solely on process-group signaling for job process reaping. Track and terminate the full descendant process tree at kill time (e.g., walk `/proc/[pid]/task/*/children` or use `ps --ppid`/`pgrep -P` recursively) in addition to sending the group signal.
- Alternatively/additionally, use Linux cgroups (already used elsewhere in the codebase for `slot_based_cgroups`) to constrain each job to a cgroup and kill via `cgroup.kill` or freezing + killing all tasks in the cgroup, which reliably catches processes regardless of `setsid`/double-fork.
- On systems without cgroups, consider PID namespace isolation for the job process so that any child (however detached) is still confined and terminated when the namespace's init process dies.

### Proof of Concept
Integration test plan (Go, `helpers/process` or `executors/custom`, Linux-only):
1. Build a driver script that `RunExec` executes, containing:
   ```sh
   setsid sh -c 'sleep 3600' >/tmp/detached.pid 2>&1 &
   echo $! > /tmp/detached_pgid
   sleep 3600
   ```
2. Start `command.Run()` with a short `ctx` timeout (or cancel `ctx` shortly after start) so `KillAndWait` path executes.
3. After `Run()` returns, read the PID recorded by the detached child and assert:
   - `syscall.Kill(pid, 0)` still returns `nil` (process alive) — proving the detached child survived job cancellation.
   - The driver process itself (`c.cmd.Process().Pid`) is confirmed dead/reaped.
4. Cleanup: manually kill the leaked PID at test teardown to avoid leaking processes in CI.

Expected (buggy) result: assertion that the detached PID is still alive succeeds, proving the orphaned process is not reaped by `KillAndWait`.

### Citations

**File:** helpers/process/job_unix.go (L16-19)
```go
func (c *osCmd) Start() error {
	setProcessGroup(c.internal)
	return c.internal.Start()
}
```

**File:** helpers/process/job_unix.go (L36-40)
```go
func setProcessGroup(c *exec.Cmd) {
	c.SysProcAttr = &syscall.SysProcAttr{
		Setpgid: true,
	}
}
```

**File:** helpers/process/job_unix.go (L42-45)
```go
func EnsureSubprocessTerminationOnExit() error {
	// Currently unsupported on non-Windows
	return nil
}
```

**File:** executors/custom/command/command.go (L89-97)
```go
	select {
	case err = <-c.waitCh:
		return err

	case <-c.context.Done():
		return newProcessKillWaiter(c.logger, c.gracefulKillTimeout, c.forceKillTimeout).
			KillAndWait(c.cmd, c.waitCh)
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
