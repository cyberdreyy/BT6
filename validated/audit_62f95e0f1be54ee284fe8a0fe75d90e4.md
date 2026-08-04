### Title
Detached process group (setsid/double-fork) escapes SIGTERM/SIGKILL scope in unixKiller - ([File: helpers/process/killer_unix.go])

### Summary
`unixKiller.Terminate`/`ForceKill` sends signals to `-pid` (the negative PID), which targets only the process group that the job's direct child process belongs to. A job script that calls `setsid` (or double-forks) creates a new, independent process group, which is not a member of that group and is therefore untouched by `syscall.Kill(pk.getPID(), ...)`.

### Finding Description
The job process is started via `osCmd.Start` in `helpers/process/job_unix.go`, which sets `SysProcAttr.Setpgid: true` [1](#0-0) [2](#0-1) . This makes the shell process itself the leader of a new process group (PGID == its own PID), and `Setpgid` alone does not propagate to further descendants that explicitly call `setsid(2)`.

`unixKiller.getPID` returns `-Pid` of the tracked `os.Process` (the direct child), and `Terminate`/`ForceKill` call `syscall.Kill(pk.getPID(), SIGTERM/SIGKILL)` [3](#0-2) [4](#0-3) . Sending a signal to a negative PID via `kill(2)` delivers it to every process whose PGID equals that value — i.e., only processes still in the shell's original process group.

A job script invoking `setsid some_long_running_command &` (or performing the classic double-fork daemonization) creates a brand-new session and process group for that descendant. From that point on, the descendant's PGID no longer matches the killer's target, so it is invisible to the group-wide `kill(-pid, ...)` call. `KillAndWait` in `helpers/process/killer.go` relies exclusively on this `Terminate`/`ForceKill` pair and has no fallback (e.g., cgroup-based kill, `/proc` scan for children, or process tree walk) to catch such orphaned/detached descendants [5](#0-4) .

There are no additional checks (allowed commands, seccomp restrictions on `setsid`, cgroup confinement) visible in this code path that would prevent an unprivileged pipeline author, under the `shell`, `custom`, or `ssh` executors (which run job scripts directly on the host without container/cgroup namespace isolation), from running `setsid`/double-fork in their own job script.

### Impact Explanation
On a shared/multi-tenant `shell`, `custom`, or `ssh` executor host, once a job detaches a background process via `setsid`, cancelling the job (`ctx` cancellation → `KillAndWait`) will terminate the shell and its still-attached children but leave the detached process running as an orphan under PID 1. That process persists indefinitely, consuming CPU, memory, disk, network sockets, or listening ports that later jobs (potentially belonging to other projects) on the same runner host may depend on or collide with — meeting the "persistent multi-tenant runner disruption that survives job cancellation" scope.

### Likelihood Explanation
This requires no special privilege beyond being able to submit a `.gitlab-ci.yml` job script, and `setsid`/double-fork are ordinary, always-available shell/coreutils primitives on Linux hosts running `shell`/`custom`/`ssh` executors. It is fully reproducible and deterministic — any job with a script step containing `setsid <cmd> &` will detach reliably. The main precondition is that the shared runner uses an executor without per-job OS-level namespace/cgroup isolation (as called out — this excludes containerized executors where PID namespaces already contain such processes), which is a common, non-exotic, supported configuration (shell/custom/ssh executors are officially supported and frequently used on bare-metal/VM shared runners).

### Recommendation
Do not rely solely on POSIX process-group signalling for job termination on the shell/custom/ssh executors. Options:
- Launch each job under its own dedicated Linux namespace or cgroup (e.g., a per-job cgroup) and kill via the cgroup's `cgroup.kill`/freeze+kill-all mechanism instead of `kill(-pgid)`, which correctly catches processes that call `setsid`.
- Alternatively, walk `/proc` to enumerate all descendants (using PPID chains, and re-parented children via `prctl(PR_SET_CHILD_SUBREACHER)` on the job's control process) rather than trusting the process-group boundary alone.
- Document and warn that `setsid`/daemonizing job scripts on shell/custom/ssh executors are not guaranteed to be cleaned up by job cancellation, and provide an opt-in cgroup-based isolation mode for shared runners.

### Proof of Concept
Go integration test extending the existing `helpers/process/killer_unix_integration_test.go` pattern:
1. Start a `Commander` running a shell script: `#!/bin/sh\nsetsid sleep 999 &\necho $!\nsleep 60`.
2. Capture the detached child's PID by having the script write the `setsid`-launched PID to a file (e.g. via `pgrep -P` immediately after backgrounding, or by parsing `ps --ppid 1` for the `sleep 999` command right after launch).
3. Call `NewOSKillWait(...).KillAndWait(command, waitCh)` to simulate job cancellation.
4. After `KillAndWait` returns, assert that `os.FindProcess(detachedPID)` + `proc.Signal(syscall.Signal(0))` returns nil (process still alive), proving the detached PID survived termination — then clean it up manually with `syscall.Kill(detachedPID, syscall.SIGKILL)` in test teardown to avoid leaking sleep processes in CI.

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

**File:** helpers/process/killer_unix.go (L21-44)
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
```

**File:** helpers/process/killer_unix.go (L46-52)
```go
// getPID will return the negative PID (-PID) which is the process group. The
// negative symbol comes from kill(2) https://linux.die.net/man/2/kill `If pid
// is less than -1, then sig is sent to every process in the process group whose
// ID is -pid.`
func (pk *unixKiller) getPID() int {
	return pk.cmd.Process().Pid * -1
}
```

**File:** helpers/process/killer.go (L63-92)
```go
// KillAndWait will take the specified process and terminate the process and
// wait util the waitCh returns or the graceful kill timer runs out after which
// a force kill on the process would be triggered.
func (kw *osKillWait) KillAndWait(command Commander, waitCh chan error) error {
	process := command.Process()
	if process == nil {
		return ErrProcessNotStarted
	}

	log := kw.logger.WithFields(logrus.Fields{
		"PID": process.Pid,
	})

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
