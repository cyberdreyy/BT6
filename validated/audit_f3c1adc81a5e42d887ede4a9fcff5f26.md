### Title
Job Object created with JOB_OBJECT_LIMIT_BREAKAWAY_OK allows CI job processes to escape kill-on-close containment on Windows - ([File: helpers/process/job_windows.go])

### Summary
`CreateJobObject` sets `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` together with `JOB_OBJECT_LIMIT_BREAKAWAY_OK`, which explicitly grants any descendant process of the job the right to detach from the job object by using `CREATE_BREAKAWAY_FROM_JOB` when creating a child process. A job process spawned this way is not tracked by the job object, so `closeJobObject()` (called from `Wait()`/`ForceKill()` on cancellation) will not terminate it. [1](#0-0) [2](#0-1) 

### Finding Description
`osCmd.Start()` creates the job object via `CreateJobObject()`, assigns the running build process to it with `AssignPidToJobObject`, then relies on `closeJobObject()` (triggered from `Wait()` or `windowsKiller.ForceKill()`) to guarantee termination of the whole process tree via `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. [3](#0-2) 

However, `CreateJobObject` also sets `JOB_OBJECT_LIMIT_BREAKAWAY_OK` with a comment that explicitly acknowledges the consequence: "Allow subprocesses to explicitly avoid termination using CREATE_BREAKAWAY_FROM_JOB." [1](#0-0)  This flag is a permission granted at the job-object level — it requires no special privilege on the caller's part. Any process running inside the job (i.e., the CI job's shell/PowerShell script or any binary it executes) can call `CreateProcess`/`CreateProcessW` with `dwCreationFlags` including `CREATE_BREAKAWAY_FROM_JOB` (e.g. via PowerShell `Add-Type` P/Invoke, a small compiled helper committed in the repo, or `cmd.exe /c start` combined with a launcher exe) to spawn a child that is never a member of the job object. Because that child was never assigned to the job object, `windows.CloseHandle(c.jobObject)` in `closeJobObject()` has no effect on it and it survives cancellation. [2](#0-1) 

The only other termination path is `windowsKiller.ForceKill()`, which additionally shells out to `taskkill /F /T /PID <pid>`. `/T` kills the target and any processes started by it based on the OS-recorded parent-child PID lineage, not job-object membership, so in some cases it may still catch a breakaway child that remains a normal child process. But this is not a robust invariant: an attacker can further detach the breakaway process from the parent's process tree (e.g., spawning it with `DETACHED_PROCESS`/`CREATE_NEW_PROCESS_GROUP` plus reparenting tricks, or having it re-parent itself once independent), and `taskkill /T` correlation is best-effort and PID-reuse-sensitive. Critically, the job-object-based guarantee that the code relies on (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) is unconditionally undermined for any process that opts out via the flag the runner itself enabled. [4](#0-3) [5](#0-4) 

This is reachable in production: the shell/custom executor Command Options wire `UseWindowsJobObject` directly from the `FF_USE_WINDOWS_JOB_OBJECT` feature flag on the job, which the runner enables specifically "to force-kill the processes" and "improve the termination of difficult-to-kill processes" — i.e., the feature's entire stated purpose is defeated by a job script that opts to escape it. [6](#0-5) [7](#0-6) 

### Impact Explanation
On a shared, autoscaled Windows shell/custom-executor host, an unprivileged pipeline author can cause a subprocess to survive job cancellation/timeout indefinitely, since the job-object kill-on-close mechanism the runner relies on for cancellation no longer applies to that process. This leads to persistent resource consumption (CPU, memory, network, disk, open handles) on the shared host beyond the lifetime of the cancelled job, potentially impacting subsequent jobs scheduled to the same instance (noisy-neighbor/DoS-style effect), and defeats the explicit safety guarantee the `FF_USE_WINDOWS_JOB_OBJECT` feature is documented to provide.

### Likelihood Explanation
Requires: (1) `FF_USE_WINDOWS_JOB_OBJECT` enabled (opt-in, not default — `DefaultValue: false`), (2) a Windows shell or custom executor runner, and (3) the ability for the job script to invoke Win32 `CreateProcess` with `CREATE_BREAKAWAY_FROM_JOB`, which is trivially reachable from an unprivileged job via PowerShell P/Invoke or a small precompiled helper binary uploaded/downloaded as part of the job's repo/artifacts. No admin rights or host compromise are needed — the breakaway permission is granted unconditionally to any child of the job by the job object's own limit flags. This is a repeatable, deterministic Windows API behavior, not a race condition. [7](#0-6) 

### Recommendation
Do not set `JOB_OBJECT_LIMIT_BREAKAWAY_OK` (nor `JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK`) when creating the per-job Windows Job Object in `CreateJobObject()`, so that `CREATE_BREAKAWAY_FROM_JOB` requests from job-spawned processes fail and all descendants remain bound to the job and subject to `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. If breakaway support is required for some legitimate use case (e.g., detached background daemons started intentionally by the runner itself, not by job scripts), gate it behind a distinct, narrowly-scoped mechanism rather than a blanket flag applied to the job object that contains the untrusted CI job's entire process tree.

### Proof of Concept
Go test (Windows-only, added near `helpers/process/killer_windows_test.go` / `job_windows.go`):
```go
func TestJobObjectBreakawayEscapesKillOnClose(t *testing.T) {
    // 1. Build/launch a "malicious" helper process via os/exec that itself
    //    calls CreateProcessW with CREATE_BREAKAWAY_FROM_JOB (via syscall)
    //    to spawn a long-running child (e.g., a sleep binary), simulating
    //    a job script escaping containment.
    cmdOpts := process.CommandOptions{UseWindowsJobObject: true, ...}
    cmd := process.NewOSCmd(exec.Command("breakaway_launcher.exe"), cmdOpts)
    require.NoError(t, cmd.Start())

    // capture the escaped child's PID (breakaway_launcher.exe writes it to stdout)
    escapedPID := readEscapedPID(t, cmd)

    waitCh := make(chan error, 1)
    go func() { waitCh <- cmd.Wait() }()

    // 2. Cancel/terminate the parent job command.
    kw := process.NewOSKillWait(logger, time.Second, time.Second)
    _ = kw.KillAndWait(cmd, waitCh)

    // 3. Assert the escaped child process is still alive (documents the bug).
    assert.True(t, processExists(escapedPID),
        "breakaway child survived job object closure — kill-on-close containment escaped")

    // Safety expectation (should fail today, pass after fix):
    // assert.False(t, processExists(escapedPID))
}
```
Expected result today: the assertion that the escaped process is still alive passes, confirming the escape; after removing `JOB_OBJECT_LIMIT_BREAKAWAY_OK`, `CreateProcess` with `CREATE_BREAKAWAY_FROM_JOB` inside the job fails and the child remains a job member, terminating on `closeJobObject()`.

### Citations

**File:** helpers/process/job_windows.go (L25-54)
```go
func (c *osCmd) Start() error {
	setProcessGroup(c.internal, c.options.UseWindowsLegacyProcessStrategy)

	if c.options.UseWindowsJobObject {
		jobObj, err := CreateJobObject()
		if err != nil {
			return fmt.Errorf("starting OS command: %w", err)
		}
		c.jobObject = jobObj
	}

	err := c.internal.Start()
	if err != nil {
		return fmt.Errorf("starting OS command: %w", err)
	}

	if c.options.UseWindowsJobObject {
		// Any failures here are ignored, since we've already started the process running.
		if err := AssignPidToJobObject(c.internal.Process.Pid, c.jobObject); err != nil {
			c.options.Logger.Warn("assigning process to job object:", err)
		}
	}
	return nil
}

func (c *osCmd) Wait() error {
	err := c.internal.Wait()
	c.closeJobObject()
	return err
}
```

**File:** helpers/process/job_windows.go (L67-74)
```go
func (c *osCmd) closeJobObject() {
	if !c.options.UseWindowsJobObject {
		return
	}
	c.once.Do(func() {
		windows.CloseHandle(c.jobObject)
	})
}
```

**File:** helpers/process/job_windows.go (L92-97)
```go
	info := windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION{
		BasicLimitInformation: windows.JOBOBJECT_BASIC_LIMIT_INFORMATION{
			LimitFlags: windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE |
				windows.JOB_OBJECT_LIMIT_BREAKAWAY_OK, // Allow subprocesses to explicitly avoid termination using CREATE_BREAKAWAY_FROM_JOB
		},
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

**File:** helpers/process/killer_windows.go (L113-115)
```go
func taskKill(pid int) error {
	return exec.Command("taskkill", "/F", "/T", "/PID", strconv.Itoa(pid)).Run()
}
```

**File:** executors/custom/custom.go (L253-257)
```go
		GracefulKillTimeout:             e.config.GetGracefulKillTimeout(),
		ForceKillTimeout:                e.config.GetForceKillTimeout(),
		UseWindowsLegacyProcessStrategy: e.Build.IsFeatureFlagOn(featureflags.UseWindowsLegacyProcessStrategy),
		UseWindowsJobObject:             e.Build.IsFeatureFlagOn(featureflags.UseWindowsJobObject),
	}
```

**File:** helpers/featureflags/flags.go (L350-357)
```go
	{
		Name:         UseWindowsJobObject,
		DefaultValue: false,
		Deprecated:   false,
		Description: "When enabled, a job object is created for each process that the runner creates on Windows " +
			"with the shell and custom executors. To force-kill the processes, the runner closes " +
			"the job object. This should improve the termination of difficult-to-kill processes.",
	},
```
