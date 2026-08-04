### Title
Windows Job Object confinement allows unprivileged build script to escape kill-on-close via `JOB_OBJECT_LIMIT_BREAKAWAY_OK` - ([File: helpers/process/job_windows.go])

### Summary
`CreateJobObject` explicitly sets `windows.JOB_OBJECT_LIMIT_BREAKAWAY_OK` alongside `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, which permits any process inside the job (including the CI build script the runner launches) to spawn a child with `CREATE_BREAKAWAY_FROM_JOB` and detach from the job object entirely. Since the build script is fully attacker-controlled (it is the CI job's own script), this defeats the kill-on-job-close guarantee that `UseWindowsJobObject` is meant to provide.

### Finding Description
`executors/shell/shell.go:Run` starts the build script via `process.NewOSCmd` with `CommandOptions.UseWindowsJobObject` gated only by the `UseWindowsJobObject` feature flag [1](#0-0) . When enabled, `osCmd.Start` calls `CreateJobObject()` and then `AssignPidToJobObject` to place the just-started build process into the job [2](#0-1) .

`CreateJobObject` sets the job's limit flags to:
```go
LimitFlags: windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE |
    windows.JOB_OBJECT_LIMIT_BREAKAWAY_OK,
``` [3](#0-2) 

`JOB_OBJECT_LIMIT_BREAKAWAY_OK` is a Win32 job-object limit flag that permits any process within the job to create child processes with the `CREATE_BREAKAWAY_FROM_JOB` flag passed to `CreateProcessW`. Any such child is not associated with the job at all, so it is not tracked and not terminated when the job is closed via `closeJobObject` on cancellation/timeout [4](#0-3) .

Because the build script (`cmd.Script`) is entirely attacker-controlled CI pipeline content, and it runs as a normal child of the job-assigned shell process, the attacker can invoke a small helper binary (or use PowerShell's `Start-Process`/native Win32 API calls via P/Invoke, or a compiled helper) that calls `CreateProcessW` with `CREATE_BREAKAWAY_FROM_JOB` for a persistent payload. That child process escapes the job hierarchy immediately, so terminating/closing the job object (on job cancel or timeout) has no effect on it.

Existing checks reviewed: there is no allow-list of subprocess creation flags, no additional job restriction, and no secondary enforcement (e.g., process tree enumeration/kill via `taskkill /T`) to catch escaped processes. `AssignPidToJobObject` and `closeJobObject` provide no such secondary defense mechanism.

### Impact Explanation
This causes persistent host-level process survival after job cancellation or timeout on Windows shell-executor hosts using `FF_USE_WINDOWS_JOB_OBJECT=true`. A malicious/compromised pipeline can plant a long-running or resource-consuming process (or one that watches for artifacts of subsequent unrelated jobs on the same shared host) that outlives the job it was launched from, defeating the runner's primary Windows process-confinement/cleanup mechanism. On shared Windows shell runners this enables persistence and disruption across jobs on the same host (though not a full sandbox/container escape, since Windows shell executor has no OS-level sandbox beyond the job object).

### Likelihood Explanation
Preconditions: runner configured for shell executor on Windows with the `UseWindowsJobObject` feature flag enabled. The attacker only needs the ability to run arbitrary script content in the pipeline (standard capability of any pipeline author), and any tool/technique (native binary, P/Invoke via PowerShell, or a small compiled helper committed to the repo) capable of calling `CreateProcessW` with `CREATE_BREAKAWAY_FROM_JOB`. This is straightforward and fully reproducible — no elevated privileges are required, since `JOB_OBJECT_LIMIT_BREAKAWAY_OK` explicitly grants this capability to any process within the job.

### Recommendation
Remove `windows.JOB_OBJECT_LIMIT_BREAKAWAY_OK` from the job's `LimitFlags` in `CreateJobObject` (`helpers/process/job_windows.go`), keeping only `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (and ideally `JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION` if desired), so that child processes cannot opt out of job termination. If breakaway is required for some legitimate nested-job scenario (e.g., `EnsureSubprocessTerminationOnExit`'s nested job support), scope that separately rather than granting it to every per-command job object wrapping arbitrary build scripts.

### Proof of Concept
Go integration test (Windows-only, mirrors `helpers/process/killer_integration_test.go` patterns):
1. Build a small helper `breakaway.exe` that calls `CreateProcessW` with `CREATE_BREAKAWAY_FROM_JOB` to spawn a long-lived child (e.g., `ping -t localhost` or a sleep loop), then writes the child PID to a file and exits.
2. Use `process.NewOSCmd("breakaway.exe", ...)` with `CommandOptions{UseWindowsJobObject: true}` to start it, call `Start()`, wait for completion via `Wait()` (which internally calls `closeJobObject`).
3. Read the child PID from the file and assert via `windows.OpenProcess` + `windows.GetExitCodeProcess` that the breakaway child is still running (`STILL_ACTIVE`) after `closeJobObject` has executed.
4. Expected (buggy) result: breakaway child PID remains alive, proving job-close does not terminate it. After applying the fix (removing `JOB_OBJECT_LIMIT_BREAKAWAY_OK`), `CreateProcessW` with `CREATE_BREAKAWAY_FROM_JOB` should fail (`ERROR_ACCESS_DENIED`) inside the helper, and no escaped process should survive.

### Citations

**File:** executors/shell/shell.go (L81-87)
```go
	cmdOpts := process.CommandOptions{
		Env:                             os.Environ(),
		Stdout:                          stdout,
		Stderr:                          stderr,
		UseWindowsLegacyProcessStrategy: s.Build.IsFeatureFlagOn(featureflags.UseWindowsLegacyProcessStrategy),
		UseWindowsJobObject:             s.Build.IsFeatureFlagOn(featureflags.UseWindowsJobObject),
	}
```

**File:** helpers/process/job_windows.go (L25-47)
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
