### Title
Process-group-based `KillAndWait` fails to terminate detached grandchild processes that escape the job's process group - ([File: helpers/process/killer_unix.go], reachable via [File: executors/custom/command/command.go])

### Finding Description
`command.Run()` starts the driver-invoked process and races `c.waitCh` (fed by `waitForCommand`, which blocks on `c.cmd.Wait()`) against `ctx.Done()`. On cancellation it calls `KillAndWait`: [1](#0-0) 

`KillAndWait` (and the underlying `unixKiller.Terminate`/`ForceKill`) do not send signals to individual PIDs; they send `SIGTERM`/`SIGKILL` to the **negative PID of the immediate child**, i.e. to the process group that was created for that child via `Setpgid: true` at `Start()`: [2](#0-1) [3](#0-2) 

`c.cmd.Wait()` (Go's `os/exec.Cmd.Wait`) only reaps and waits for the single directly-started process, not descendants. If the attacker-controlled job script (executed via `run_exec`) spawns a grandchild that detaches from the original process group — e.g. via `setsid`, a double-fork, or manually calling `setpgid(0,0)` — that grandchild is no longer a member of the group targeted by `kill(-pid, SIGTERM/SIGKILL)`. Once the immediate child (e.g. the shell wrapper) exits or is killed, `cmd.Wait()` returns, `waitForCommand` pushes the result onto `c.waitCh`, and `Run()`/`KillAndWait()` report success/failure — while the escaped grandchild keeps running under a different process group on the same host, completely undetected by the runner's kill logic.

This is a genuine gap in the "kill everything before Cleanup" invariant: no PID/group enumeration (e.g. via `/proc` tree walk, cgroup, or PID namespace) is used to catch processes that changed their group — only a single `kill(-pid, sig)` call.

### Impact Explanation
On host-process executors that rely on this kill path (custom executor via `run_exec`, and the same pattern in the shell executor), an unprivileged CI job author can leave a background process running past job termination and `Cleanup`. Because build directories on shared runner concurrency slots are deterministically reused per `{runner-token}/{concurrency-id}/{namespace}/{project}` (as documented for shell/custom/ssh build directory layout), a subsequent job scheduled into the same concurrency slot (e.g., a later pipeline run for the same project, or in misconfigured shared setups) can execute alongside — or have its workspace polluted/observed by — the still-running orphaned process from the prior job. This is a real cross-job persistence/isolation weakness in the OS-level process cleanup guarantee, not merely a documented "shared-host trust" caveat, because the runner explicitly promises to terminate job-spawned processes via `KillAndWait` before proceeding.

### Likelihood Explanation
Highly feasible and fully attacker-triggerable with an unprivileged CI job: any job script that runs `setsid some-long-running-binary &`, or a two-stage double-fork wrapper, will detach from the process group set up by `setProcessGroup`. No special privileges, container escape, or admin misconfiguration are required — only that the job commands execute as a normal OS process on the runner host (true for the custom and shell executors' `run_exec`/script execution path). This is deterministic and repeatable on every run.

### Recommendation
Do not rely solely on `kill(-pgid, sig)`. Track and reap all descendants of the started process (e.g., walk `/proc/<pid>/task/*/children` recursively, or use Linux PID namespaces / cgroups to scope and kill the entire process tree regardless of process-group membership) before considering `KillAndWait` complete, and before signaling job completion on `c.waitCh`. Alternatively, force all job-spawned processes into an isolated cgroup or PID namespace at start time so a `cgroup.kill` (or PID-namespace teardown) is guaranteed to catch group-escaping descendants.

### Proof of Concept
Go integration test in `executors/custom/command`:
1. Configure `Options`/`process.CommandOptions` to run a wrapper script equivalent to:
   `sh -c 'setsid sh -c "trap '' TERM; while true; do sleep 1; done" & sleep 0.2'`
2. Cancel the `context.Context` shortly after start to trigger `KillAndWait`.
3. Assert `Run()` returns without hanging (i.e., `KillAndWait` "succeeds").
4. After `Run()` returns, scan `/proc` (or use `pgrep`) for the detached `sleep`-loop process by name/marker and assert **it is still alive** — proving that a process outlives `KillAndWait`'s reported termination, violating the "no job-spawned process survives Cleanup" invariant.

### Citations

**File:** executors/custom/command/command.go (L81-97)
```go
func (c *command) Run() error {
	err := c.cmd.Start()
	if err != nil {
		return fmt.Errorf("failed to start command: %w", err)
	}

	go c.waitForCommand()

	select {
	case err = <-c.waitCh:
		return err

	case <-c.context.Done():
		return newProcessKillWaiter(c.logger, c.gracefulKillTimeout, c.forceKillTimeout).
			KillAndWait(c.cmd, c.waitCh)
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

**File:** helpers/process/job_unix.go (L16-40)
```go
func (c *osCmd) Start() error {
	setProcessGroup(c.internal)
	return c.internal.Start()
}

func (c *osCmd) Wait() error {
	return c.internal.Wait()
}

func (c *osCmd) Process() *os.Process {
	return c.internal.Process
}

func newOSCmd(c *exec.Cmd, options CommandOptions) Commander {
	return &osCmd{
		internal: c,
		options:  options,
	}
}

func setProcessGroup(c *exec.Cmd) {
	c.SysProcAttr = &syscall.SysProcAttr{
		Setpgid: true,
	}
}
```
