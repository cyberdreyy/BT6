This confirms the reported behavior is real but is a known, documented limitation rather than a newly discovered bug.

### Title
Job cancellation kills only the process group, not detached (setsid) grandchildren - ([File: helpers/process/killer_unix.go])

### Summary
`setProcessGroup` in `helpers/process/job_unix.go` sets `Setpgid: true` on job scripts, and `unixKiller.getPID`/`Terminate`/`ForceKill` in `helpers/process/killer_unix.go` send signals to the negative PID (the process group). A subprocess spawned by the job script that calls `setsid()` (or otherwise detaches into a new process group) leaves that group and is not reached by `syscall.Kill(-pgid, ...)`, so it survives job cancellation/`KillAndWait`. [1](#0-0) [2](#0-1) 

### Finding Description
`osCmd.Start()` calls `setProcessGroup(c.internal)`, which sets `SysProcAttr.Setpgid = true` so the shell/custom executor's script becomes the leader of a new process group. [3](#0-2) 
On cancellation, `osKillWait.KillAndWait` calls `processKiller.Terminate()`/`ForceKill()`, which resolve to `unixKiller.getPID()` returning `-Pid` and issuing `syscall.Kill(-pid, SIGTERM/SIGKILL)` — a process-group-wide signal per `kill(2)` semantics. [4](#0-3) [5](#0-4) 

Any script executed by the shell or custom executor is attacker-controlled CI script content. If that script forks a child that calls `setsid(2)` (e.g., via `setsid some-command &`, or a compiled helper doing `Setsid: true`), the child becomes the leader of a brand-new session and process group, decoupled from the job's group. `kill(-pgid, sig)` targeting the original job's group no longer reaches it. There is no code in this repo that walks `/proc` for actual descendant PIDs, uses cgroups, or otherwise catches processes that escape the pgid — the entire termination mechanism relies solely on process-group signaling.

This is not a Windows-specific limitation only: `EnsureSubprocessTerminationOnExit` is explicitly a no-op on non-Windows platforms. [6](#0-5) 
The only integration test asserting orphan-termination behavior (`ensure_subprocess_termination_integration_test.go`) is gated to `windows` builds and targets Windows Job Objects, not the Unix pgid killer — so this pgid-escape gap is untested on Unix. [7](#0-6) 

### Impact Explanation
On a shared/multi-tenant Unix runner (shell or custom executor), a job author's script can leave behind a running process after the job is cancelled, times out, or the runner otherwise triggers `KillAndWait`. This process can persist beyond the job lifecycle, consuming host resources or running arbitrary long-lived attacker code on the host, surviving what the runner believes is a fully-terminated job.

### Likelihood Explanation
Feasible with a one-line CI script (`setsid sleep 999999 &` or equivalent) on any host using the shell or custom executor, since `setsid` is a standard, non-privileged Linux tool. This is deterministic and fully repeatable — no race condition or timing dependency is needed since group detachment happens synchronously in the forked child.

### Recommendation
On Unix, don't rely solely on `Setpgid`+group kill. Options: track and kill actual descendant PIDs via `/proc/<pid>/task/*/children` (or `getpgid` scanning) recursively; or, where available, use Linux cgroups (already partially used by `helpers/cgroup` in some executors) to freeze/kill the entire cgroup tree the job's cgroup contains, which captures processes regardless of `setsid`. This is the standard mitigation GitLab Runner already applies via cgroups for some executors — extending it (or an equivalent PID-tree walk) to the generic shell/custom executor kill path would close the gap.

### Proof of Concept
Add a Unix integration test analogous to the existing Windows one:
```go
//go:build integration && linux

func TestKillAndWaitDoesNotKillDetachedGrandchild(t *testing.T) {
    // script: writes its PID, then runs `setsid sleep 60 &`, writes grandchild PID, then sleeps
    cmd := process.NewOSCmd(exec.Command("bash", "-c", script), opts)
    require.NoError(t, cmd.Start())
    // read job PID and detached grandchild PID from a temp file/pipe
    waitCh := make(chan error, 1)
    go func() { waitCh <- cmd.Wait() }()

    kw := process.NewOSKillWait(logger, 1*time.Second, 1*time.Second)
    err := kw.KillAndWait(cmd, waitCh)
    require.NoError(t, err)

    // assert the job's own pgid is dead
    require.Error(t, syscall.Kill(jobPID, 0))
    // assert the detached grandchild is STILL alive - proving escape from pgid kill
    require.NoError(t, syscall.Kill(grandchildPID, 0))
}
```
Expected result confirming the bug: the grandchild PID check succeeds (process still alive) after `KillAndWait` returns success, demonstrating the escape.

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

**File:** helpers/process/killer.go (L66-91)
```go
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
```

**File:** helpers/process/ensure_subprocess_termination_integration_test.go (L1-1)
```go
//go:build integration && windows
```
