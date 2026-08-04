### Title
Detached grandchild processes (via `setsid()`) escape process-group signal delivery in `KillAndWait` - ([File: helpers/process/killer_unix.go])

### Summary
`unixKiller.Terminate`/`ForceKill` send `SIGTERM`/`SIGKILL` to `-pid` (the negative PID targeting the job's process group), relying on the fact that `NewOSCmd` sets `Setpgid: true` so the entire process tree shares one process group. Any process in that tree that calls `setsid()` creates a new session and, as a POSIX side effect, a new process group, removing itself (and its descendants) from the original group and thus from the reach of the group-directed signal.

### Finding Description
`NewOSCmd`/`osCmd.Start()` sets `SysProcAttr{Setpgid: true}` [1](#0-0)  so the job script process becomes the leader of a new process group equal to its own PID. When cancellation occurs, `osKillWait.KillAndWait` calls `processKiller.Terminate()`/`ForceKill()` [2](#0-1) , which resolve to `unixKiller.getPID()` returning `-pid` and issuing `syscall.Kill(-pid, SIGTERM/SIGKILL)` [3](#0-2) . `kill(2)` with a negative PID targets every process whose **process group ID** equals `pid`; it is fundamentally a process-group-scoped signal, not a process-tree-scoped one. If any process in the job's tree calls `setsid()`, POSIX semantics dictate it becomes the leader of a brand-new session and process group (its PGID becomes its own PID), detaching it from the original group. Consequently `syscall.Kill(-originalPID, ...)` no longer reaches that detached subtree, and it survives both the graceful `SIGTERM` and the escalation to `SIGKILL`.

An unprivileged job script has full control over its own shell content and can trivially invoke `setsid <command> &` (the `setsid` utility or a small program calling the syscall directly) with no special privileges required — `setsid()` is available to any unprivileged process. No allowed-image, path, or auth check in Runner mitigates this, since the mitigation would need to occur at the OS process-management layer (e.g., cgroups, PID namespaces, or catching new process groups), and no such mechanism exists in `helpers/process`.

### Impact Explanation
On shell/custom/ssh executors, where jobs execute directly on a shared host without OS-level process isolation (no cgroup/PID-namespace containment), a cancelled or timed-out job can leave a detached process running indefinitely, consuming CPU, memory, disk, or network resources on a runner host that is reused by subsequent jobs from potentially different projects/users. This violates the invariant that "cancelling a job must terminate all processes it spawned," and creates persistent, multi-tenant resource-exhaustion/availability risk that is directly attacker-triggerable from ordinary job script content.

### Likelihood Explanation
This is trivially and repeatably reachable by any user who can supply `.gitlab-ci.yml` script content and whose pipeline runs on a shell, custom, or ssh executor without additional host-level sandboxing (cgroups/systemd-run/PID namespace). No race condition or privilege escalation is required — merely `setsid` plus backgrounding, which is standard POSIX behavior and requires no special capability.

### Recommendation
Do not rely solely on process-group signaling. Options: (1) launch job processes inside a Linux cgroup (freezer/unified cgroup) dedicated to the job and kill via the cgroup, which reaches processes regardless of session/group changes; (2) enumerate the full process tree via `/proc` (parent-child relationships) rather than only the process group and signal each PID individually, then re-scan after signaling to catch newly detached descendants; (3) where available, use PID namespaces (already used in some executors) so that any orphaned process becomes unreachable and is reaped when the namespace's init dies. Document/limit this risk explicitly on the shell/ssh/custom executors, since they lack namespace isolation and are the only affected combination.

### Proof of Concept
Integration test (extends `helpers/process/killer_unix_integration_test.go` style tests):
```go
func TestKiller_DetachedGrandchildSurvivesGroupKill(t *testing.T) {
    // Start a Commander running a shell script:
    //   setsid sh -c 'sleep 999999999' &
    //   sleep 999999999   # parent stays alive so job isn't instantly done
    cmd := NewOSCmd("/bin/sh", []string{"-c",
        "setsid sh -c 'sleep 999999999 & echo $! > /tmp/detached.pid' & sleep 999999999"},
        CommandOptions{...})
    require.NoError(t, cmd.Start())

    waitCh := make(chan error, 1)
    go func() { waitCh <- cmd.Wait() }()

    kw := NewOSKillWait(logger, 1*time.Second, 1*time.Second)
    err := kw.KillAndWait(cmd, waitCh)
    // err is expected KillProcessError or timeout-related, since the
    // parent group is killed but grandchild may keep waitCh from closing early;
    // primary assertion is below.

    detachedPID := readPIDFromFile("/tmp/detached.pid")
    time.Sleep(500 * time.Millisecond)
    assert.True(t, processAlive(detachedPID),
        "expected detached grandchild to survive group kill, demonstrating escape")
    // cleanup
    syscall.Kill(detachedPID, syscall.SIGKILL)
}
```
Expected result: `processAlive(detachedPID)` returns `true` after `KillAndWait` returns, proving the detached process is not terminated by the process-group-scoped kill, confirming the bug.

### Citations

**File:** helpers/process/job_unix.go (L16-40)
```go
func (c *osCmd) Start() error {
	setProcessGroup(c.internal)
	return c.internal.Start()
}

func (c *osCmd) Wait() error {
	return c.internal.Wait()
}

func (c *osCmd) Process() *os.Process {
	return c.internal.Process
}

func newOSCmd(c *exec.Cmd, options CommandOptions) Commander {
	return &osCmd{
		internal: c,
		options:  options,
	}
}

func setProcessGroup(c *exec.Cmd) {
	c.SysProcAttr = &syscall.SysProcAttr{
		Setpgid: true,
	}
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
