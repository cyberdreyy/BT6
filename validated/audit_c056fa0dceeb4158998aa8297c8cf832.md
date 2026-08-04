### Title
Windows job object explicitly permits `CREATE_BREAKAWAY_FROM_JOB`, letting a job script escape `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` termination - ([File: helpers/process/job_windows.go])

### Summary
`CreateJobObject` in `helpers/process/job_windows.go` sets `JOB_OBJECT_LIMIT_BREAKAWAY_OK` alongside `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, with a comment explicitly acknowledging that this "allows subprocesses to explicitly avoid termination using CREATE_BREAKAWAY_FROM_JOB". A child process spawned by an unprivileged job script with `CREATE_BREAKAWAY_FROM_JOB` is removed from the job object and therefore is not guaranteed to be terminated when the job object handle is closed.

### Finding Description
`CreateJobObject()` builds a `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` with: [1](#0-0) 
`JOB_OBJECT_LIMIT_BREAKAWAY_OK` is a documented Windows flag that permits any process created with `CREATE_BREAKAWAY_FROM_JOB` to detach from the job object at creation time. Once detached, that process (and its own descendants) is no longer subject to `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, meaning `windowsKiller.ForceKill` -> `c.closeJobObject()` will not terminate it via the job-object mechanism: [2](#0-1) 

However, `ForceKill` does not rely solely on the job object. It first calls `taskkill /F /T /PID <pid>`: [3](#0-2) 
`taskkill /T` walks the OS-level process tree (parent-child PID relationships tracked by the Windows kernel), which is entirely independent of Job Object membership. A breakaway child spawned directly by the job's shell process remains a child process in that PID tree (breakaway only removes job-object association, it does not detach the parent-child relationship), so `taskkill /T` will still enumerate and kill it in the common case where the job's root process is still alive and the child hasn't otherwise orphaned itself before the kill runs.

The scenario described in the question — a breakaway child surviving job cancellation — is only reproducible when the child additionally severs its OS-level parent-child linkage or its ancestor process already exited before `taskKill` executes (e.g., via double-fork-like techniques, reparenting through a third-party broker process such as WMI `Win32_Process.Create`, or a race where the immediate parent shell exits before `taskkill /T` runs). That is a considerably narrower and more elaborate attack than "just call `CreateProcess` with `CREATE_BREAKAWAY_FROM_JOB`", which by itself is caught by the `/T` tree-kill fallback.

### Impact Explanation
If an attacker successfully both (a) sets `CREATE_BREAKAWAY_FROM_JOB` and (b) detaches the process from the OS-level parent-child tree before `taskKill` runs, the resulting orphaned process is not guaranteed to be killed by either the job object (bypassed) or `taskkill /T` (tree already broken), and could persist past job cancellation, consuming host CPU/memory shared with other tenants on the same runner host. This matches the scoped impact of persistent, cross-job resource consumption.

### Likelihood Explanation
Reaching this requires more than the simple breakaway call assumed in the question: the attacker must additionally defeat the process-tree-based `taskkill /T` fallback, which is not part of the job-object mechanism at all and requires a separate technique (process reparenting, timing race with parent exit, or spawning through an unrelated broker process). This is feasible in principle on Windows but is a non-trivial, timing-dependent exploit chain rather than the single-flag defect implied by the question. The precondition also requires `UseWindowsJobObject` to be enabled (shell/custom executor on Windows), which narrows applicability further.

### Recommendation
- Remove `JOB_OBJECT_LIMIT_BREAKAWAY_OK` from the job object limit flags in `CreateJobObject` (`helpers/process/job_windows.go`) so that `CREATE_BREAKAWAY_FROM_JOB` requests from job-spawned processes are denied by the OS, closing this bypass at the source rather than relying on the secondary `taskkill /T` tree-walk as the only backstop.
- If breakaway is required for a legitimate use case, restrict it and add an explicit, tested guarantee (e.g., enumerate and force-kill any process whose creation time falls within the job's lifetime and whose original parent PID matches the job's root, independent of job-object membership) so orphaned/breakaway descendants cannot evade both mechanisms simultaneously.

### Proof of Concept
Go integration test outline (Windows-only, `helpers/process/killer_integration_test.go` style):
1. Build a test helper binary that, on start, spawns a grandchild process using `CREATE_BREAKAWAY_FROM_JOB` and then immediately exits itself (to break the OS-level parent-child chain before `taskkill /T` can walk it), while the grandchild sleeps for e.g. 30s and writes a heartbeat file.
2. Start this helper via `process.NewOSCmd` with `UseWindowsJobObject: true`.
3. Call `windowsKiller.ForceKill()` (or drive through `KillAndWait`) once the grandchild is confirmed running.
4. Assert: the grandchild PID is still alive (e.g., via `OpenProcess` + `GetExitCodeProcess` returning `STILL_ACTIVE`) after `ForceKill`/`KillAndWait` returns, proving termination is not guaranteed for detached breakaway descendants.

Note: because this depends on winning a timing race against `taskkill /T`'s tree walk (or using a reparenting broker), the PoC is inherently non-deterministic without the additional detachment step; a PoC using only `CREATE_BREAKAWAY_FROM_JOB` without severing the OS parent-child link would be expected to still be killed by `taskkill /T`, and should be included as a negative-control assertion.

### Citations

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
