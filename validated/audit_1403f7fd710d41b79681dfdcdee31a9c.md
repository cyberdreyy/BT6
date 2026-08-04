### Title
`windowsKiller.Terminate()` escalates to `ForceKill`/`taskkill /F /T` on cosmetic multierror partial failures instead of only genuine termination failures - ([File: helpers/process/killer_windows.go])

### Summary
`taskTerminate` (helpers/process/killer_windows.go:60-111) aggregates four independent Windows API calls into a single `*multierror.Error` and returns non-nil if *any* of them fails — including the two post-signal cleanup calls (`FreeConsole`/`AttachConsole` re-attach, `SetConsoleCtrlHandler` restore) that only restore the runner's own console state and have no bearing on whether the target process was actually signaled. `Terminate()` (line 31-42) treats any non-nil return as full failure and immediately calls `ForceKill()`, which runs `taskkill /F /T` synchronously, with no grace period.

### Finding Description
In the `UseWindowsLegacyProcessStrategy` branch, `taskTerminate` performs, in order: `GenerateConsoleCtrlEvent` (the actual graceful-termination signal), then `FreeConsole`, `attachConsole` (to parent), and `SetConsoleCtrlHandler` restore — the latter three exist purely to restore the Runner process's own console state and are appended into the same `multierror.Error` via `multierror.Append` regardless of whether the earlier signal call succeeded [1](#0-0) . `errors.ErrorOrNil()` returns non-nil if *any* appended error is non-nil, so a failure in the cosmetic restore steps is indistinguishable from a failure in the actual `GenerateConsoleCtrlEvent` signal delivery.

`Terminate()` only checks `if err := taskTerminate(...); err != nil` and unconditionally calls `pk.ForceKill()` in that branch [2](#0-1) . Unlike the normal `osKillWait.KillAndWait` flow, which waits `gracefulKillTimeout` (10 minutes) before calling `ForceKill()` [3](#0-2) , this internal escalation inside `Terminate()` happens immediately and synchronously — with essentially no time for the target process to run its own cleanup after receiving the CTRL event. `ForceKill()` then runs `taskkill /F /T` (force, tree) against the same PID [4](#0-3) [5](#0-4) .

`Terminate()` is invoked by `KillAndWait` on job timeout or cancellation, both of which are actions an ordinary pipeline author can trigger on their own job (setting a short timeout or manually canceling) [6](#0-5) , and this path is used by the shell executor [7](#0-6) .

### Impact Explanation
Because the restore-console failure path collapses the entire grace period, a cosmetic, non-fatal error in the console-restore substeps causes `taskkill /F /T` to fire nearly instantly after the CTRL event was already delivered successfully, racing the forced kill against the target process's own in-flight cleanup/exit handlers. If that process was mid-write to the shared build or cache directory for that project/concurrency slot, forced termination can leave locked or partial files behind, which is picked up by a subsequent job reusing the same build/cache path (per-project, per-concurrent-id directory layout documented in `docs/executors/shell.md`).

### Likelihood Explanation
Requires `UseWindowsLegacyProcessStrategy` feature flag enabled (this is the branch with multiple chained kernel32 calls) and any transient failure in one of the non-signal substeps (e.g., `AttachConsole`/`SetConsoleCtrlHandler` restore failing due to timing/console state races, which are plausible on Windows since they depend on console ownership state that can change between calls). The triggering action (job timeout or cancellation) is fully attacker-controlled by the job's own author and requires no special privilege, so the precondition is reachable, though the intermediate substep failure is environment-dependent rather than deterministically reproducible by the attacker.

### Recommendation
In `taskTerminate`, distinguish the actual signal-delivery error (`GenerateConsoleCtrlEvent`) from the cosmetic restore-step errors: return the signal error (or nil) as the authoritative result determining whether `Terminate()` should escalate to `ForceKill`, while logging/aggregating the restore-step errors separately without affecting the escalation decision. Alternatively, have `Terminate()` inspect the underlying multierror for which sub-call failed rather than treating any non-nil error as full failure.

### Proof of Concept
Unit test in `helpers/process` (windows-only build tag):
1. Stub/mock the kernel32 function wrapper used inside `taskTerminate` so that `GenerateConsoleCtrlEvent` returns success (res1 != 0) but the subsequent `SetConsoleCtrlHandler` restore call is forced to fail.
2. Call `windowsKiller.Terminate()` on a running test process.
3. Assert `ForceKill()`/`taskKill` (i.e., `taskkill /F /T`) is NOT invoked, since the actual termination signal succeeded.
4. Current implementation fails this assertion because `taskTerminate` returns a non-nil `multierror.Error` from the failed restore step, causing `Terminate()` to call `ForceKill()` unconditionally.

### Citations

**File:** helpers/process/killer_windows.go (L36-41)
```go
	if err := taskTerminate(pk.cmd.Process().Pid, pk.cmd.options.UseWindowsLegacyProcessStrategy); err != nil {
		pk.logger.Warn("Failed to terminate process:", err)

		// try to kill right-after
		pk.ForceKill()
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

**File:** helpers/process/killer_windows.go (L92-110)
```go
	// always attempt to restore console and Ctrl-C handler for runner process
	// so collect any errors together instead of returning early
	var errors *multierror.Error

	if UseWindowsLegacyProcessStrategy {
		errors = multierror.Append(errors, generateConsoleCtrlEvent(
			"send Ctrl-C event to process being terminated", uintptr(windows.CTRL_C_EVENT), uintptr(pid)))
		errors = multierror.Append(errors, freeConsole(
			"detach the runner process from the console of the terminated process"))
		errors = multierror.Append(errors, attachConsole(
			"attach the runner process to the console of its parent process", uintptr(math.MaxUint32)))
		errors = multierror.Append(errors, setConsoleCtrlHandler(
			"restore Ctrl-C event handler for runner process", uintptr(unsafe.Pointer(nil)), uintptr(0)))
	} else {
		errors = multierror.Append(errors, generateConsoleCtrlEvent(
			"send Ctrl-Break event to process being terminated", uintptr(windows.CTRL_BREAK_EVENT), uintptr(pid)))
	}

	return errors.ErrorOrNil()
```

**File:** helpers/process/killer_windows.go (L113-115)
```go
func taskKill(pid int) error {
	return exec.Command("taskkill", "/F", "/T", "/PID", strconv.Itoa(pid)).Run()
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
