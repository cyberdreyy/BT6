### Title
Race condition in `taskTerminate` console attach/detach breaks per-job CTRL_C signal isolation on Windows legacy process strategy - (File: helpers/process/killer_windows.go)

### Summary
`taskTerminate` manipulates process-wide Windows console state (`FreeConsole`/`AttachConsole`/`SetConsoleCtrlHandler`) with no synchronization primitive protecting the sequence. Because a single `gitlab-runner` process can execute multiple concurrent jobs (e.g. shell executor, or multiple concurrent jobs runs in the same runner service process), two simultaneous job cancellations that both hit `taskTerminate` with `UseWindowsLegacyProcessStrategy=true` can interleave their console attach/detach calls, causing a `CTRL_C_EVENT` intended for one job's process group to be delivered to whatever console the runner process happens to be attached to at that instant - potentially the other job's process.

### Finding Description
`taskTerminate` in [1](#0-0)  performs, when `UseWindowsLegacyProcessStrategy` is true: `FreeConsole()` (detaches the calling process from whatever console it's currently attached to), `AttachConsole(pid)` (attaches the calling process - process-wide, not per-thread - to the target job's console), disables the runner's own Ctrl handler, then generates the `CTRL_C_EVENT`, and finally restores state via `FreeConsole()` + `AttachConsole(ATTACH_PARENT_PROCESS)` + re-enabling the handler at [2](#0-1) .

`AttachConsole`/`FreeConsole` operate on the calling *process*, not a thread, and a process can be attached to only one console at a time (per the Win32 console API model). There is no mutex, lock, or other synchronization guarding this function - a `grep` for `sync.Mutex`/`sync.` in `helpers/process/*.go` shows no lock used in `killer_windows.go`, and `taskTerminate` is called independently from `windowsKiller.Terminate()` at [3](#0-2)  for every job's kill path with no shared coordination. If two jobs are being cancelled concurrently in the same runner process, their `taskTerminate` calls can interleave arbitrarily: Job A's `AttachConsole(pidA)` can be immediately followed by Job B's `FreeConsole()`/`AttachConsole(pidB)` before Job A calls `GenerateConsoleCtrlEvent`, so Job A's `CTRL_C_EVENT` gets delivered into Job B's console/process group instead of A's, or vice versa. The restore step (`AttachConsole(ATTACH_PARENT_PROCESS)`) is likewise racy against the other goroutine's in-flight sequence.

### Impact Explanation
Impact is a correctness/isolation defect: a job cancellation signal meant for one job's process can be delivered to a concurrently-terminating unrelated job's process (or fail to reach the intended process), and the runner's own console reattachment state can end up incorrect. This violates the expectation that job termination/signal handling is isolated per job. It is not privilege escalation or cross-tenant data exposure by itself, but it can cause spurious signal delivery/incorrect cancellation behavior between two jobs running concurrently on the same Windows runner host under the legacy process strategy.

### Likelihood Explanation
`UseWindowsLegacyProcessStrategy` is a documented feature flag path (`FF_USE_WINDOWS_LEGACY_PROCESS_STRATEGY`, referenced in `helpers/featureflags/flags.go` and consumed via `executors/shell/shell.go` and `executors/custom/custom.go`), which is not the default in modern runner configurations, and requires the operator to run this legacy mode plus have multiple concurrently-cancelled jobs on the same runner host - a normal, not attacker-elevated, operational scenario (concurrent job cancellations are routine). Since the race depends purely on timing of two legitimate job-cancel paths within the same runner process, it is reproducible under load without any special attacker privilege beyond triggering two job cancellations, but it only manifests when this legacy strategy is enabled.

### Recommendation
Serialize all `FreeConsole`/`AttachConsole`/`SetConsoleCtrlHandler`/`GenerateConsoleCtrlEvent` sequences in `taskTerminate` with a process-wide `sync.Mutex` so only one job's console-attach/signal/restore sequence executes at a time, ensuring the runner process is never attached to a different job's console while sending or the calling job's console while another job's send is in-flight.

### Proof of Concept
Go integration test (extending `helpers/process/killer_integration_test.go`) with `UseWindowsLegacyProcessStrategy: true`:
1. Start two sleep-binary child processes (`process.NewOSCmd`) each configured to print a unique marker to stdout/stderr upon receiving `CTRL_C_EVENT`.
2. Concurrently call `k1.Terminate()` and `k2.Terminate()` in separate goroutines with minimal/no delay between them (loop many iterations to increase interleave probability).
3. Assert each process's captured output contains only its own marker, and that neither process's Ctrl handler received the other's marker (or that one process' termination silently failed while the other received it twice), demonstrating cross-delivery/lost-delivery of the `CTRL_C_EVENT` due to interleaved `AttachConsole` state.

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

**File:** helpers/process/killer_windows.go (L60-90)
```go
func taskTerminate(pid int, UseWindowsLegacyProcessStrategy bool) error {
	kernel32 := windows.NewLazySystemDLL("kernel32.dll")
	if err := kernel32.Load(); err != nil {
		return fmt.Errorf("failed to load kernel32: %w", err)
	}

	kernel32Function := func(methodName string) func(string, ...uintptr) error {
		return func(description string, args ...uintptr) error {
			if res1, _, callErr := kernel32.NewProc(methodName).Call(args...); res1 == 0 {
				return fmt.Errorf("failed to %s: %w", description, callErr)
			}
			return nil
		}
	}

	freeConsole := kernel32Function("FreeConsole")
	attachConsole := kernel32Function("AttachConsole")
	setConsoleCtrlHandler := kernel32Function("SetConsoleCtrlHandler")
	generateConsoleCtrlEvent := kernel32Function("GenerateConsoleCtrlEvent")

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
```

**File:** helpers/process/killer_windows.go (L96-104)
```go
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
