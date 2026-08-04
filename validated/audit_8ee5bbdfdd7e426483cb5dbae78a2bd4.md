### Title
Job-cancellation kill path never applies tree-kill/job-close to `CREATE_BREAKAWAY_FROM_JOB` children when the main process exits promptly from `CTRL_BREAK` - (File: `helpers/process/killer_windows.go`, `helpers/process/job_windows.go`, `helpers/process/killer.go`)

### Summary
`CreateJobObject` explicitly sets `JOB_OBJECT_LIMIT_BREAKAWAY_OK`, letting any unprivileged job-script child process opt out of the job object via `CREATE_BREAKAWAY_FROM_JOB`. The only mechanism that could still catch such an escaped process — `taskkill /T` inside `windowsKiller.ForceKill` — is skipped in the common cancellation path because `windowsKiller.Terminate` (via `taskTerminate`'s `CTRL_BREAK` signal) usually makes the tracked main process exit quickly, which makes `KillAndWait`'s `waitCh` fire before the graceful timeout, so `ForceKill` (and its `taskkill /T`) is never called.

### Finding Description
`CreateJobObject` in [1](#0-0)  sets `LimitFlags` to `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK`, with a comment explicitly acknowledging that it "allows subprocesses to explicitly avoid termination using `CREATE_BREAKAWAY_FROM_JOB`." Any process the job script launches with that creation flag (reachable via `Add-Type`/P-Invoke `CreateProcess` from an ordinary PowerShell or batch script — no admin rights required) is removed from job-object membership at creation time, so `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` will never terminate it.

`osCmd.Wait` closes the job handle as soon as the tracked (`internal.Wait()`) process exits: [2](#0-1)  — this only affects processes still inside the job, not breakaway children.

On cancellation, `KillWaiter.KillAndWait` first calls `Terminate()`, which invokes `taskTerminate` (send `CTRL_BREAK_EVENT`/console signal) and only falls back to `ForceKill()` if `taskTerminate` itself returns a Go error: [3](#0-2) . `ForceKill` is the only place that calls `taskkill /F /T /PID` (a tree-based kill independent of job-object membership) before closing the job object: [4](#0-3) .

In `KillAndWait`, `ForceKill` is reached only if `waitCh` (fed by `cmd.Wait()`) does not fire within `gracefulKillTimeout`: [5](#0-4) . In the ordinary cancellation case, `taskTerminate`'s console signal causes the tracked shell process (cmd.exe/powershell.exe) to exit promptly, `cmd.Wait()` returns quickly, `waitCh` fires before the timeout, and `ForceKill` (hence `taskkill /T`) is never invoked. Since the breakaway child is not in the job object and `taskkill /T` — the only tree-aware kill — is skipped, the breakaway child is never targeted by any termination mechanism and keeps running indefinitely after the job is reported cancelled.

### Impact Explanation
A single unprivileged job script can leave an arbitrary long-running process (e.g., a resource-intensive loop, network listener, or persistent agent) alive on the shared Windows runner host after the job that spawned it has been cancelled and reported finished. Because Windows shell/custom-executor runners commonly run multiple jobs from different projects/tenants on the same host, this degrades host resources (CPU, memory, ports, disk) for unrelated subsequent jobs, matching the scoped "cross-job persistent disruption" impact.

### Likelihood Explanation
Preconditions are realistic and low-effort: Windows shell/custom executor, `UseWindowsJobObject` feature flag enabled, and a job script capable of running PowerShell/batch (default for shell executors). Spawning a `CREATE_BREAKAWAY_FROM_JOB` child requires only a short P/Invoke snippet in the job's own script — no elevated privileges, no special executor configuration beyond the documented feature flag. The behavior is deterministic given the code path (`Terminate` succeeding quickly enough to short-circuit `ForceKill`), so it is repeatable, not merely theoretical/race-dependent.

### Recommendation
Always perform the tree-based kill (or otherwise enumerate/terminate descendants regardless of job-object membership) on job cancellation, not only when `ForceKill` is separately triggered. Concretely: call `taskkill /T` (or an equivalent descendant-enumeration kill) unconditionally as part of cancellation/cleanup (e.g., inside `osCmd.Wait`/`closeJobObject`, or always in `KillAndWait` regardless of which branch of the select fires), rather than only inside `ForceKill`'s error-fallback path. Alternatively, drop `JOB_OBJECT_LIMIT_BREAKAWAY_OK` unless a specific, narrowly-scoped feature genuinely requires it, since its stated purpose (avoiding hard-to-kill process trees) directly undermines the guarantee that job cancellation kills all descendants.

### Proof of Concept
Go integration test (Windows) sketch, alongside `helpers/process/killer_windows_integration_test.go`:
```go
func TestKiller_BreakawayChildSurvivesCancellation(t *testing.T) {
    // Build a helper binary that spawns a child process with
    // CREATE_BREAKAWAY_FROM_JOB (via syscall CreateProcess) that
    // sleeps/loops for a long duration, then exits itself quickly
    // (simulating a shell process reacting to CTRL_BREAK).
    k, _, cmd, cleanup, _ := newKillerWithLoggerAndCommand(t, /* useWindowsJobObject */ true)
    defer cleanup()

    waitCh := make(chan error)
    go func() { waitCh <- cmd.Wait() }()

    time.Sleep(1 * time.Second)
    k.Terminate() // simulates job cancellation -> CTRL_BREAK, main proc exits fast

    err := <-waitCh
    assert.NoError(t, err) // main process exited quickly, ForceKill never invoked

    // Assert the breakaway child (PID recorded by helper binary, e.g. via
    // a temp file it writes on start) is STILL running.
    childPID := readChildPIDFromTempFile(t)
    assert.True(t, processIsAlive(childPID), "breakaway child should be dead but is still running")
}
```
Expected result under current code: the assertion fails (child still alive), confirming `ForceKill`/`taskkill /T` was never invoked and the breakaway child outlives job cancellation.

### Citations

**File:** helpers/process/job_windows.go (L50-54)
```go
func (c *osCmd) Wait() error {
	err := c.internal.Wait()
	c.closeJobObject()
	return err
}
```

**File:** helpers/process/job_windows.go (L92-96)
```go
	info := windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION{
		BasicLimitInformation: windows.JOBOBJECT_BASIC_LIMIT_INFORMATION{
			LimitFlags: windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE |
				windows.JOB_OBJECT_LIMIT_BREAKAWAY_OK, // Allow subprocesses to explicitly avoid termination using CREATE_BREAKAWAY_FROM_JOB
		},
```

**File:** helpers/process/killer_windows.go (L31-42)
```go
func (pk *windowsKiller) Terminate() {
	if pk.cmd.Process() == nil {
		return
	}

	if err := taskTerminate(pk.cmd.Process().Pid, pk.cmd.options.UseWindowsLegacyProcessStrategy); err != nil {
		pk.logger.Warn("Failed to terminate process:", err)

		// try to kill right-after
		pk.ForceKill()
	}
}
```

**File:** helpers/process/killer_windows.go (L44-55)
```go
func (pk *windowsKiller) ForceKill() {
	if pk.cmd.Process() == nil {
		return
	}

	err := taskKill(pk.cmd.Process().Pid)
	if err != nil {
		pk.logger.Warn("Failed to force-kill:", err)
	}

	pk.cmd.closeJobObject()
}
```

**File:** helpers/process/killer.go (L66-92)
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
}
```
