### Title
Windows job cancellation is not terminal — `JOB_OBJECT_LIMIT_BREAKAWAY_OK` + single-pass `taskkill /T` let a self-respawning job process survive `KillAndWait` - (`File: helpers/process/killer_windows.go`, `helpers/process/job_windows.go`)

### Summary
The Windows job-object safety net used by the runner is explicitly configured with `JOB_OBJECT_LIMIT_BREAKAWAY_OK`, which lets any child process opt out of the job via `CREATE_BREAKAWAY_FROM_JOB`. Combined with `ForceKill`'s single, non-verified `taskkill /F /T` pass and `closeJobObject`'s reliance on job membership, an attacker-controlled job process that reacts to the non-legacy `CTRL_BREAK_EVENT` termination signal by spawning a detached, breakaway child can outlive both the graceful termination and the force-kill steps, persisting on the shared host after cancellation completes.

### Finding Description
`taskTerminate` (`helpers/process/killer_windows.go:60-111`), in the default (non-legacy) branch, only sends `CTRL_BREAK_EVENT` to the process group via `GenerateConsoleCtrlEvent` [1](#0-0) . This is a cooperative signal: nothing prevents the target process from installing a console control handler, catching the event, and instead of exiting, spawning a new process before it dies.

`CreateJobObject` (`helpers/process/job_windows.go:86-108`) sets `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` together with `JOB_OBJECT_LIMIT_BREAKAWAY_OK`, explicitly allowing any descendant to escape job membership via `CREATE_BREAKAWAY_FROM_JOB` [2](#0-1) . Because job membership is the mechanism `closeJobObject` relies on to guarantee cleanup (`windows.CloseHandle(c.jobObject)` only terminates processes still associated with the job) [3](#0-2) , any breakaway child is permanently outside that safety net.

`ForceKill` (`helpers/process/killer_windows.go:44-55`) performs exactly one `taskKill(pid)` call (`taskkill /F /T /PID <pid>`) and then closes the job object once [4](#0-3) . There is no verification that the process tree is actually gone, and no repeated/looping enumeration. `KillAndWait` (`helpers/process/killer.go:66-92`) calls `Terminate()` once, waits `gracefulKillTimeout` (default `GracefulTimeout = 10m`), then calls `ForceKill()` exactly once and returns [5](#0-4) .

Attack flow: a pipeline author supplies a job (shell/custom executor) whose workload, on receiving `CTRL_BREAK_EVENT`, spawns a watchdog child with `CREATE_BREAKAWAY_FROM_JOB` (permitted by the job's own limit flags) and `CREATE_NEW_PROCESS_GROUP`/`DETACHED_PROCESS`, then exits. During the up-to-10-minute graceful window the watchdog can itself repeat this pattern indefinitely, and because it was created with breakaway it was never added to the job object, so `closeJobObject` cannot reach it. The single `taskkill /T` pass at `ForceKill` time only catches whatever is in the enumerated process tree at that instant; a continuously respawning payload can have already produced generations that are no longer discoverable as children of the original PID (e.g., by fully detaching parentage), and no follow-up pass exists to catch newer generations spawned after the scan.

Existing protections are insufficient here: `JOB_OBJECT_LIMIT_BREAKAWAY_OK` is a deliberate opt-in weakening of job-object containment, and there is no retry/verification loop after `ForceKill` to assert the tree is actually terminated before `KillAndWait` returns.

### Impact Explanation
A single malicious/compromised job can leave a persistent, self-perpetuating process running on the shared Windows runner host after the job is cancelled and `KillAndWait` returns successfully. This consumes CPU/memory/disk indefinitely and degrades capacity for other tenants' jobs scheduled on the same host — a concrete violation of the invariant that cancellation must be terminal.

### Likelihood Explanation
Preconditions match realistic defaults: `UseWindowsLegacyProcessStrategy=false` (default, non-legacy CTRL_BREAK path) and `UseWindowsJobObject=true` with `JOB_OBJECT_LIMIT_BREAKAWAY_OK` unconditionally set whenever job objects are used [2](#0-1) . Any pipeline author who can run an executable (shell executor, custom executor invoking arbitrary binaries) controls the job's process behavior on signal receipt, so the attacker input is simply "the compiled/scripted workload the pipeline runs." This is repeatable on every job cancellation against the same host.

### Recommendation
- Stop granting `JOB_OBJECT_LIMIT_BREAKAWAY_OK` by default for job objects that back CI job execution; if breakaway is required for a specific legitimate use case, gate it and compensate with independent enumeration-based cleanup.
- After `ForceKill`, verify termination: re-enumerate descendants (e.g., via `NtQuerySystemInformation`/`CreateToolhelp32Snapshot` walking on the original job/PID and any known breakaway children) and repeat the kill pass until no descendants remain or a hard timeout/error is surfaced, instead of a single unverified `taskkill /T` call.
- Consider tracking spawned descendants independently of job-object membership (e.g., by polling the process tree during the graceful window) so breakaway children are still identified and killed.

### Proof of Concept
Integration test (Windows, `helpers/process/killer_windows_integration_test.go` style):
1. Build a test binary (similar to `helpers/process/testdata/sleep/main.go`) that: on `CTRL_BREAK_EVENT`, spawns a copy of itself with `CREATE_BREAKAWAY_FROM_JOB|CREATE_NEW_PROCESS_GROUP|DETACHED_PROCESS`, passing a "respawn-on-signal" flag, then exits.
2. Start it via `process.NewOSCmd` with `UseWindowsJobObject: true`, `UseWindowsLegacyProcessStrategy: false`.
3. Call `KillAndWait` (or `Terminate()` then simulate the graceful timeout then `ForceKill()`), then wait the full `GracefulTimeout + KillTimeout` window.
4. Assert: no process matching the watchdog binary's image name exists in the system process list after the wait window (e.g., via `tasklist /FI "IMAGENAME eq <name>"`), expecting the assertion to fail against current code (a lingering respawned process is found), demonstrating the bug.

### Citations

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

**File:** helpers/process/killer_windows.go (L105-108)
```go
	} else {
		errors = multierror.Append(errors, generateConsoleCtrlEvent(
			"send Ctrl-Break event to process being terminated", uintptr(windows.CTRL_BREAK_EVENT), uintptr(pid)))
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

**File:** helpers/process/killer.go (L76-91)
```go
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
