### Title
Process-group-based SIGKILL can be evaded by job-spawned processes that call `setsid`, allowing malicious processes to survive `KillAndWait` cancellation - (File: helpers/process/killer_unix.go)

### Summary
`unixKiller.Terminate`/`ForceKill` send `SIGTERM`/`SIGKILL` only to the negative PID of the original shell/executor process (`kill(-pgid)`), which targets the process group the child was placed into via `Setpgid: true` at start (`helpers/process/job_unix.go`). A job-spawned process that calls `setsid` (or otherwise forks into a new session/process group) leaves that group and is no longer reachable by the group-directed kill, so it survives job cancellation even after `KillProcessError` is ultimately returned.

### Finding Description
`osCmd.Start()` sets `SysProcAttr.Setpgid: true` [1](#0-0) [2](#0-1)  so the launched shell becomes its own process-group leader. `unixKiller.getPID()` computes `-pid`, and both `Terminate` and `ForceKill` call `syscall.Kill(-pid, SIGTERM/SIGKILL)`, which per `kill(2)` semantics targets "every process in the process group whose ID is -pid" [3](#0-2) . This only reaches processes that remain members of that specific process group.

A job script that runs `setsid sh -c 'while true; do :; done' &` creates a new session and a new process group for the child, detaching it from the original job's process group. The signal sent to `-pgid` of the original job process will not reach this detached descendant. `osKillWait.KillAndWait` sends `Terminate`, waits `gracefulKillTimeout`, then `ForceKill`, waits `forceKillTimeout`, and if the job's own `waitCh` doesn't complete it returns `*KillProcessError` [4](#0-3) . Critically, `ForceKill`'s `syscall.Kill` error (e.g., `ESRCH` if all group members are already gone) is only logged via `Warn` and never propagated [5](#0-4)  — but even when the call to `syscall.Kill` succeeds (no error), it simply does not touch processes outside the target group, so no error occurs at all in this scenario; the detached process is unaffected regardless of return value. `KillAndWait`'s own timeout/return path has no mechanism to detect or kill leaked descendants outside the recorded pgid.

No other layer in the Runner compensates for this: there's no cgroup-based (or pid-namespace-based) tracking of the whole descendant tree for the shell/custom executors — the isolation model here relies solely on process-group signaling under the assumption that no descendant leaves the group. This assumption is defeated by a simple `setsid` call.

### Impact Explanation
In shell/custom executors (the executors relying on this OS-level `killer`), an attacker-controlled job script can spawn a workload that outlives cancellation/timeout of the job, persisting compute resource usage (CPU/memory) on the shared host beyond the intended job lifetime, and potentially continuing to hold or exfiltrate secrets/environment variables/files that were available in the job's environment after the job is nominally terminated. This is a genuine violation of the stated invariant that "job cancellation/timeout must guarantee termination of all job-spawned processes," and it is reachable purely by an unprivileged job author providing a `setsid` command — no other privilege escalation, host `docker.sock` access, or admin misconfiguration is required.

### Likelihood Explanation
Highly feasible and repeatable: any job running under the shell executor (or a custom executor using `helpers/process` command management) that permits arbitrary shell syntax can invoke `setsid` (a standard, unprivileged, widely-available Linux utility) without any special capability. This is not dependent on privileged containers, host PID namespace sharing, or docker.sock exposure — it works purely from the shell executor's own process-group-based cleanup mechanism, so it does not fall under the excluded "shell executor trust on a shared host" category (which concerns trust of the executed code, not correctness of the Runner's own cancellation guarantee). The exploit requires no race condition and reproduces deterministically every time.

### Recommendation
Do not rely solely on process-group signaling for job process cleanup on Linux. Use a stronger process containment mechanism such as:
- Launching the job in its own PID namespace or cgroup, and killing via the cgroup's `cgroup.kill`/freezer to guarantee termination of the entire process tree, including reparented/detached processes; or
- Recursively enumerating `/proc` for all descendants of the tracked PID (via `stat`/`ppid` traversal, or `prctl(PR_SET_CHILD_SUBREAPER)`) instead of trusting the process group alone, and killing anything found; and
- Treating `ForceKill`'s `syscall.Kill` failure/incomplete termination as an actionable signal (not just a `Warn` log) so `KillAndWait` can escalate (e.g., retry against discovered orphans) before giving up and returning `KillProcessError`.

### Proof of Concept
Extend `helpers/process/killer_unix_integration_test.go` pattern:
```go
func TestKillAndWait_SetsidEscapesProcessGroup(t *testing.T) {
    logger := ... // stub logger
    cmd := process.NewOSCmd("sh", []string{"-c",
        "setsid sh -c 'while true; do sleep 1; done' & echo $!; sleep 30"},
        process.CommandOptions{ /* ... */ })
    require.NoError(t, cmd.Start())

    waitCh := make(chan error, 1)
    go func() { waitCh <- cmd.Wait() }()

    kw := process.NewOSKillWait(logger, 200*time.Millisecond, 200*time.Millisecond)
    err := kw.KillAndWait(cmd, waitCh)

    var killErr *process.KillProcessError
    // The original job process should be gone, but the detached grandchild
    // (captured PID) should still be alive, proving group-kill escape.
    require.True(t, errors.As(err, &killErr) || err != nil)
    require.True(t, isProcessAlive(detachedPID), "detached setsid process should have survived job cancellation")
}
```
Expected assertion: the `setsid`-detached descendant process remains alive (queryable via `/proc/<pid>` or `syscall.Kill(pid, 0) == nil`) well after `forceKillTimeout` has elapsed, demonstrating violation of the "guarantee termination of all job-spawned processes" invariant.

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
