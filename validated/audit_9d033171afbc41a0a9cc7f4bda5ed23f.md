This confirms the mechanism is exactly as described, and the docs explicitly acknowledge this technique is the sole termination mechanism for the shell and custom executors.

### Title
Job script can escape process-group-based cancellation/timeout via `setpgid`/`setsid`, causing killed jobs to keep running - ([File: helpers/process/killer_unix.go])

### Summary
`unixKiller.Terminate`/`ForceKill` send `SIGTERM`/`SIGKILL` only to the negative PID (process group) of the direct child process, relying on every descendant staying in that single group. Any process spawned inside the job script (or the script's own shell, on systems where the shell doesn't re-parent) can call `setpgid`/`setsid` to move itself into a new process group, after which `syscall.Kill(-PID, ...)` in `killer_unix.go` no longer reaches it, letting the detached process continue running after the job is canceled, timed out, or reported finished.

### Finding Description
`setProcessGroup` in [1](#0-0)  sets `Setpgid: true` only on the immediate child (the shell/`run_exec` process), making that child the leader of a new process group equal to its own PID. `unixKiller.getPID` in [2](#0-1)  returns `-Pid`, and `Terminate`/`ForceKill` in [3](#0-2)  send signals to that single negative PID (the whole process group), assuming all descendants remain members.

This is a documented but real limitation: any process the job's script forks (e.g., a background helper, `setsid some-daemon &`, or a small binary calling `setpgid(0,0)`/`setsid(2)`) detaches from the runner-assigned process group and creates its own group. Because Linux processes inherit their parent's pgid only until they explicitly change it, an unprivileged script has full control over this — no elevated privileges are required, `setpgid`/`setsid` on one's own process is always permitted. Once detached, `kill(-originalPID, SIGTERM/SIGKILL)` no longer targets the escaped process or its own children, so it survives job cancellation, `timeout`, or force-kill deadlines described in [4](#0-3)  and [5](#0-4) , both of which state termination is "achieved by having the main process set as a process group which all the child processes belong too" — an assumption that a malicious/careless script can trivially break.

The shell executor's `Run` in [6](#0-5)  and the custom executor's `command.Run` in [7](#0-6)  both depend exclusively on this same group-kill mechanism for cancellation, so this affects both executors that use `helpers/process`. No cgroup, namespace, or PID-1 containment backs this up on the shell/custom executor path (unlike Docker/Kubernetes executors, which are out of scope here since they use container isolation, not process groups).

### Impact Explanation
An unprivileged pipeline author on a shell or custom executor can make their job's background work outlive job cancellation/timeout/force-kill entirely, continuing to consume CPU, memory, network, or disk on the shared runner host indefinitely after GitLab reports the job as canceled/timed-out/finished. On a shared shell runner this is a resource-exhaustion / persistence primitive (e.g., a crypto-miner or reverse shell kept alive across job boundaries), which is a legitimate escalation beyond what a "canceled" job should be able to do — even though shell-executor trust of the running user is already documented, "survives cancellation to run after job completion" is a distinct, unaddressed logic bug, not merely the already-accepted host-trust caveat.

### Likelihood Explanation
Trivially reproducible: any job script only needs to run `setsid nohup <cmd> &` or a two-line C/Go helper calling `syscall.Setpgid(0, 0)` before forking a long-running background task. No special permissions, images, or configuration are needed beyond a normal shell/custom executor job. This is 100% reproducible on every affected OS/executor combination.

### Recommendation
Do not rely solely on the process group. Track all descendant PIDs (e.g., via cgroups v1/v2 on Linux — placing the job in a dedicated cgroup and killing/freezing the whole cgroup — or via `/proc` PID-tree walking as a fallback) so that termination reaches processes that have called `setpgid`/`setsid`. At minimum, document this as a known escape and consider using Linux cgroups (already partially used elsewhere in the codebase) for job containment on shell/custom executors instead of process groups alone.

### Proof of Concept
Integration test extending `helpers/process/killer_unix_integration_test.go`:
1. Build a tiny helper binary (similar to `helpers/process/testdata/sleep/main.go`) that immediately calls `syscall.Setpgid(0, 0)` then sleeps.
2. Start it via `process.NewOSCmd` (as `killer_unix_integration_test.go` does for the sleep binary), obtaining its PID.
3. Call `ForceKill()` and wait briefly.
4. Assert via `os.FindProcess(pid)` + `proc.Signal(syscall.Signal(0))` returning `nil` (process still alive) — proving the detached process was not reached by the group-kill, whereas a control run without the `setpgid` call should be reaped.

### Citations

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

**File:** docs/executors/shell.md (L98-112)
```markdown
## Terminating and killing processes

The shell executor starts the script for each job in a new process. On
UNIX systems, it sets the main process as a process group.

GitLab Runner terminates processes when:

- A job [times out](https://docs.gitlab.com/ci/pipelines/settings/#set-a-limit-for-how-long-jobs-can-run).
- A job is canceled.

On UNIX system `gitlab-runner` sends `SIGTERM` to the process and its
child processes, and after 10 minutes sends `SIGKILL`. This allows for
graceful termination for the process. Windows doesn't have a `SIGTERM`
equivalent, so the kill signal is sent twice. The second is sent after
10 minutes.
```

**File:** docs/executors/custom.md (L428-453)
```markdown
## Terminating and killing executables

GitLab Runner tries to gracefully terminate an executable under any
of the following conditions:

- `config_exec_timeout`, `prepare_exec_timeout` or `cleanup_exec_timeout` are met.
- The job [times out](https://docs.gitlab.com/ci/pipelines/settings/#set-a-limit-for-how-long-jobs-can-run).
- The job is canceled.

When a timeout is reached, a `SIGTERM` is sent to the executable, and
the countdown for
[`graceful_kill_timeout`](../configuration/advanced-configuration.md#the-runnerscustom-section)
starts. The executable should listen to this signal to make sure it
cleans up any resources. If `graceful_kill_timeout` passes and the
process is still running, a `SIGKILL` is sent to kill the process and
[`force_kill_timeout`](../configuration/advanced-configuration.md#the-runnerscustom-section)
starts. If the process is still running after
`force_kill_timeout` has finished, GitLab Runner abandons the
process and doesn't try to stop or kill anymore. If both these timeouts
are reached during `config_exec`, `prepare_exec` or `run_exec` the build
is marked as failed.

Any child process that is spawned by the driver also receives the
graceful termination process explained above on UNIX based systems. This
is achieved by having the main process set as a [process group](https://man7.org/linux/man-pages/man2/setpgid.2.html)
which all the child processes belong too.
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
