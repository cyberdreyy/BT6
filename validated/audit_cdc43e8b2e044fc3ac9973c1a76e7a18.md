### Title
Concurrent `taskTerminate` calls race on process-wide console/Ctrl-handler state - ([File: helpers/process/killer_windows.go])

### Summary
`taskTerminate` in `helpers/process/killer_windows.go` mutates process-wide Windows console state (`FreeConsole`/`AttachConsole`/`SetConsoleCtrlHandler`) with no synchronization, and is invoked once per job cancellation via `windowsKiller.Terminate()` → `osKillWait.KillAndWait()`. When two jobs are canceled concurrently on the same Windows host with the legacy strategy enabled, their goroutines can interleave calls against this single shared process-wide state, producing a genuine data race with no locking.

### Finding Description
`taskTerminate(pid, UseWindowsLegacyProcessStrategy)` [1](#0-0)  is reached from `windowsKiller.Terminate()` [2](#0-1) , which is invoked from `osKillWait.KillAndWait()` whenever a build's context is canceled (job timeout, cancel action, etc.) [3](#0-2) . Each `KillAndWait` runs in its own goroutine per job (see the `shell` and `custom` executors' `select { case <-cmd.Context.Done(): ... KillAndWait(...) }` pattern) [4](#0-3) [5](#0-4) .

When `UseWindowsLegacyProcessStrategy` is enabled, `taskTerminate` calls `FreeConsole`, `AttachConsole(pid)`, and `SetConsoleCtrlHandler(nil, 1)` to detach the runner from its own console and attach to the target process's console before disabling the runner's Ctrl-C handling, then later calls `GenerateConsoleCtrlEvent`, `FreeConsole`, `AttachConsole(parent)`, `SetConsoleCtrlHandler(nil, 0)` to restore state [6](#0-5) . These Win32 APIs operate on per-process, not per-thread, state: a process can only be attached to one console at a time, and `SetConsoleCtrlHandler` toggles a single process-wide flag. No mutex or other synchronization guards this sequence, so if two jobs are terminated concurrently on the same runner host with the legacy flag on, their `AttachConsole`/`FreeConsole`/`SetConsoleCtrlHandler` calls can interleave arbitrarily.

### Impact Explanation
If two concurrent jobs both execute `taskTerminate`, one job's `AttachConsole` can fail because the process is already attached elsewhere, or the restore/disable steps from different goroutines can interleave, leaving the runner process's Ctrl-C handler state or console attachment in an unintended state during the race window. This can cause spurious termination failures (falling back to `ForceKill`/`taskkill /F /T` — see the `err != nil` branch in `Terminate()`) [7](#0-6) , and in the worst case a transient window where the runner's own Ctrl-C handling is left disabled by one goroutine while another goroutine's sequence proceeds. The code always executes the restoration calls (`multierror.Append`-based, not short-circuited) regardless of intermediate errors [8](#0-7) , which is a partial mitigation for single-call failures but does not protect against concurrent invocation ordering.

### Likelihood Explanation
This requires `FF_USE_WINDOWS_LEGACY_PROCESS_STRATEGY=true`, which is **not the default** since GitLab Runner 16.10 and is explicitly documented as deprecated/discouraged specifically because it interferes with graceful termination: "To successfully and gracefully drain a Windows Runner, this feature flag should be set to `false`" [9](#0-8) . It also only applies to Windows shell/custom executors [10](#0-9) . Triggering it further requires two jobs to be canceled/killed at nearly the same instant on the same runner host — an unprivileged job author can force their own job into cancellation (e.g., timeout), but they cannot control when a second, unrelated job is also canceled, so reliable exploitation to durably disable operator Ctrl-C handling is not deterministic; it is a genuine but narrow, opt-in-only race window rather than a default-on, reliably-triggerable multi-tenant DoS.

### Recommendation
Add a package-level `sync.Mutex` (or similar serialization) around the console-attach/handler-toggle sequence in `taskTerminate` so only one goroutine can mutate the runner process's console state at a time; alternatively, accelerate deprecation/removal of `UseWindowsLegacyProcessStrategy` since it is already discouraged and off by default.

### Proof of Concept
Add a `go test -race` test in `helpers/process` (Windows-only build tag) that starts two long-running processes, launches `taskTerminate` for each concurrently from separate goroutines with `UseWindowsLegacyProcessStrategy=true`, and asserts:
1. No data race reported by `-race`.
2. After both goroutines return, `SetConsoleCtrlHandler(nil, 0)` state is verifiably restored (e.g., by sending a synthetic Ctrl event to the runner test process and confirming it is handled), independent of interleaving order.

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

**File:** helpers/process/killer_windows.go (L60-108)
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
```

**File:** helpers/process/killer.go (L66-78)
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

```

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

**File:** executors/custom/command/command.go (L89-96)
```go
	select {
	case err = <-c.waitCh:
		return err

	case <-c.context.Done():
		return newProcessKillWaiter(c.logger, c.gracefulKillTimeout, c.forceKillTimeout).
			KillAndWait(c.cmd, c.waitCh)
	}
```

**File:** helpers/featureflags/flags.go (L150-160)
```go
	{
		Name:            UseWindowsLegacyProcessStrategy,
		DefaultValue:    false,
		Deprecated:      false,
		ToBeRemovedWith: "",
		Description: "In GitLab Runner 16.10 and later, the default is `false`. In GitLab Runner 16.9 and earlier, the default is `true`. " +
			"When disabled, processes that Runner creates on Windows (shell and custom executor) will be " +
			"created with additional setup that should improve process termination. When set to `true`, legacy " +
			"process setup is used. To successfully and gracefully drain a Windows Runner, this feature flag should " +
			"be set to `false`.",
	},
```
