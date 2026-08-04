### Title
Concurrent `taskTerminate` calls race on global console attach state, causing cross-job signal misdelivery - (File: helpers/process/killer_windows.go)

### Summary
`taskTerminate` mutates process-wide console state via `FreeConsole`/`AttachConsole`/`SetConsoleCtrlHandler` with no locking, and is invoked per-job from `windowsKiller.Terminate()`. When `UseWindowsLegacyProcessStrategy` is enabled and two jobs are cancelled concurrently on the same runner process, the interleaved kernel32 calls can cause one job's `GenerateConsoleCtrlEvent`/`SetConsoleCtrlHandler` to act on the wrong PID's console, or leave the runner detached from either console.

### Finding Description
`taskTerminate(pid, UseWindowsLegacyProcessStrategy)` performs a strictly sequential, unsynchronized series of global Win32 calls: `FreeConsole()` (detaches the calling process, i.e. the whole runner, from whatever console it's currently attached to), `AttachConsole(pid)` (attaches the runner to `pid`'s console), `SetConsoleCtrlHandler(NULL, TRUE)` (disables Ctrl handling for the runner as a whole process), then `GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)`, followed by cleanup calls that free/reattach/restore state [1](#0-0) .

All of this state — the console the runner process is attached to, and the runner-wide Ctrl handler — is per-process, not per-goroutine or per-job. `windowsKiller.Terminate()` is called independently for each job's kill goroutine with no shared mutex or serialization in `killer_windows.go`, `job_windows.go`, or `commander.go` [2](#0-1) . If goroutine A calls `AttachConsole(pidA)` and then, before A calls `GenerateConsoleCtrlEvent`, goroutine B calls `FreeConsole()` followed by `AttachConsole(pidB)`, the runner process becomes attached to job B's console. A's subsequent `GenerateConsoleCtrlEvent(CTRL_C_EVENT, pidA)` targets a PID that isn't the currently-attached console, and depending on kernel32 semantics can fail, silently no-op, or (since `GenerateConsoleCtrlEvent` sends to all processes sharing the currently attached console group) affect the wrong console/process group — i.e., job B's process instead of, or in addition to, job A's. Likewise `SetConsoleCtrlHandler(NULL, TRUE/FALSE)` toggles a single process-wide flag, so B's restore call (`FALSE`) can re-enable Ctrl handling for the runner while A still expects it disabled, or vice versa, changing whether the generated Ctrl event is actually delivered.

No mutex, atomic, or queueing mechanism protects this critical section; each `taskTerminate` call independently performs "detach → attach → disable handler → signal → detach → reattach-to-parent → restore handler" against process-global OS state with no serialization against concurrent invocations.

### Impact Explanation
On multi-job Windows shell-executor runners with `UseWindowsLegacyProcessStrategy` enabled, concurrent cancellations of two unrelated jobs can result in: job A's cancellation being silently swallowed (its termination signal is delivered to job B's console or to no process at all), and/or job B's process incorrectly receiving a Ctrl-C intended for job A. This is a correctness/isolation bug affecting job cancellation reliability across jobs sharing a runner host — not privilege escalation or artifact/data leakage, but a real cross-job interference in termination handling as scoped.

### Likelihood Explanation
Requires: (1) a Windows runner processing multiple concurrent jobs via the shell/custom executor, (2) `UseWindowsLegacyProcessStrategy` feature flag/option enabled, and (3) two jobs being cancelled or timing out within the same narrow window. This is a legacy compatibility path (the default modern path uses `CREATE_NEW_PROCESS_GROUP` + `CTRL_BREAK_EVENT` per-process-group, which does not touch shared console-attach state, see the `else` branch at lines 105-108), so exposure is limited to runners that still rely on the legacy strategy. Given normal GitLab CI usage (multiple concurrent jobs on one runner is common, and job cancellations/timeouts happening close together across jobs is plausible under load or pipeline-wide cancel), the race is realistically triggerable by an unprivileged user simply by cancelling/timing-out jobs, without needing coordination with another attacker — concurrent legitimate job activity from any users on the shared runner suffices.

### Recommendation
Serialize all `taskTerminate` invocations with a package-level mutex (or a dedicated console-state manager) so only one legacy termination sequence runs at a time across the whole runner process, e.g. `var legacyTerminateMu sync.Mutex` held for the full duration of the attach/signal/restore sequence in `taskTerminate`. Alternatively, migrate fully off the legacy console-attach strategy (the non-legacy `CTRL_BREAK_EVENT` + process-group path already avoids shared global state) and deprecate `UseWindowsLegacyProcessStrategy` for concurrent-job runners.

### Proof of Concept
Go test in `helpers/process` (windows-only, integration-tagged):
1. Start two long-sleeping `osCmd` processes (A, B) with `UseWindowsLegacyProcessStrategy: true` using the existing `testdata/sleep` binary.
2. Launch two goroutines simultaneously calling `taskTerminate(pidA, true)` and `taskTerminate(pidB, true)`.
3. Wait on both processes with a timeout and record which process(es) actually exited and within what time.
4. Repeat the run N times (e.g., 100 iterations) to amplify the race window.
5. Assertions: both A and B must exit due to their own `GenerateConsoleCtrlEvent` call (verify via distinct exit codes/markers written by the sleep binary distinguishing "received CTRL_C" vs "killed by taskkill fallback"), and no run should show A's terminate causing B to exit (or vice versa) or either job hanging past the timeout requiring `ForceKill` fallback — a failure of this assertion in any iteration demonstrates the cross-job race.

### Citations

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

**File:** helpers/process/killer_windows.go (L80-104)
```go
	if UseWindowsLegacyProcessStrategy {
		if err := freeConsole("detach the runner process from its console"); err != nil {
			return err
		}
		if err := attachConsole("attach to the console of the process being terminated", uintptr(pid)); err != nil {
			return err
		}
		if err := setConsoleCtrlHandler("disable Ctrl-C event handler for runner process", uintptr(unsafe.Pointer(nil)), uintptr(1)); err != nil {
			return err
		}
	}

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
```
