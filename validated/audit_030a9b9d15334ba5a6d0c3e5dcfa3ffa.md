### Title
Job cancellation in the step-runner execution path does not reclaim resources held by detached grandchild processes - ([File: functions/concrete/run/stages/step.go], [File: functions/concrete/run/env/env.go])

### Summary
`shell()` in `functions/concrete/run/stages/step.go` writes an attacker-controlled `Step.Script` to a temp file and runs it via `e.Command`, which calls `gracefulexitcmd.New` without ever setting `SysProcAttr`/`Setpgid` on the underlying `exec.Cmd`. A script that daemonizes a child (e.g. `setsid ./listener &`, double-fork, `disown`) produces a process that is not part of any group the runner can kill, so it survives both normal completion and `ctx` cancellation, leaking CPU/memory/ports on the shared host past job teardown.

### Finding Description
`Step.Run` builds a shell/pwsh script from user-supplied `Step.Script` and calls `shell(ctx, e, script, s.Step)` [1](#0-0) . `shell` writes it to a temp file and executes it via `e.Command(ctx, cmd, envVars, args...)` [2](#0-1) .

`Env.Command` constructs the process with `gracefulexitcmd.New(ctx, e.GracefulExitDelay, name, args...)` and only sets `Dir`, `Env`, `Stdout`, `Stderr` — no `SysProcAttr` is configured to place the child in its own process group: [3](#0-2) . The code comments in `normalizeExitError` explicitly acknowledge this: a "backgrounded child" can outlive `gracefulexitcmd`'s `WaitDelay` while holding the parent's stdio pipes, and the job is still reported as successful because the exit code of the direct child is what's checked — the runner does not wait for or account for such descendants [4](#0-3) . Cancellation-driven `SIGTERM` from `gracefulexitcmd.Cmd.Cancel` is explicitly noted as targeting only that single command's termination path, not a process group [5](#0-4) .

This contrasts with the runner's existing, established pattern for containing subprocess trees: `helpers/process/job_unix.go` explicitly sets `Setpgid: true` via `SysProcAttr` before starting a command so the whole group can be terminated together [6](#0-5) . The step-runner code path (`functions/concrete/run/...`, used for the newer `run:`/steps execution model) does not use this mechanism at all, so a script executed through `Step.Script` can fork a child that detaches from the parent's process group/session (e.g. via `setsid`), causing it to be reparented to init on the host rather than being killed alongside the script when `ctx` is canceled or the script exits.

Attacker input: the content of `Step.Script` is fully controlled by the pipeline/job author (a normal unprivileged GitLab user authoring `.gitlab-ci.yml` steps). No special executor privilege is required — any script forking a detached, ignoring-SIGTERM child is sufficient.

### Impact Explanation
On a shared runner host/VM (e.g. shell executor, or a docker-machine/autoscaled VM reused across jobs), a canceled or completed job can leave behind processes that keep running: holding TCP listener ports, consuming CPU/memory, or leaving files/locks in shared paths. A subsequent job — potentially from a different project sharing that same host/VM instance — can then fail to bind the same port or suffer resource starvation, and the cancellation UI/state falsely implies all resources were reclaimed. This is a direct violation of the stated invariant that "cancellation must be sufficient to reclaim all resources the job acquired."

### Likelihood Explanation
Feasibility is high: an attacker only needs a step whose script is `setsid sh -c 'exec 200<>/dev/null; nc -l 9999 & disown'` (or an equivalent double-fork/`nohup` pattern) to detach a child from the runner-managed process. This requires no elevated privileges, no image/service abuse, and works against any executor where `Step.Script` runs a real OS process with `Env.Command` (this is the step-runner integration path, not the legacy shell-executor path that already applies `Setpgid`). It is fully repeatable per job.

### Recommendation
Mirror the existing `helpers/process` pattern in `functions/concrete/run/env/env.go`: set a `SysProcAttr` with `Setpgid: true` (Unix) / an equivalent job object (Windows) on the `exec.Cmd` produced by `gracefulexitcmd.New`, and on cancellation/cleanup send the termination signal to the negative PID (process group) instead of only the direct child, then reap remaining descendants before declaring the job's resources reclaimed.

### Proof of Concept
Integration test sketch:
1. Set `Step.Script` to a shell script that backgrounds a detached TCP listener via `setsid`/double-fork on a fixed port `P`, then sleeps indefinitely (independent of the parent).
2. Run `Step.Run(ctx, e)` with a context that is canceled shortly after the script starts (simulating job cancellation).
3. Assert `Step.Run` returns (cancellation/timeout error) within `GracefulExitDelay`.
4. After `Step.Run` returns, attempt `net.Listen("tcp", ":P")` in the test process (simulating a subsequent job's bind attempt).
5. Expected (bug present): bind fails with "address already in use" because the detached listener process is still alive, proving cancellation did not reclaim the port.
6. After applying the `Setpgid`+group-kill fix, re-run the same test and assert the bind now **succeeds**, proving the fix reclaims resources.

### Citations

**File:** functions/concrete/run/stages/step.go (L25-50)
```go
func (s Step) Run(ctx context.Context, e *env.Env) error {
	if len(s.Script) == 0 {
		return nil
	}

	if !s.shouldRun(e) {
		e.Debugf("Skipping step %s: not applicable for current job status", s.Step)
		return nil
	}

	sw := scriptwriter.New(s.Step, e.Shell)
	sw.DebugTrace = s.Debug
	sw.ExitCodeCheck = s.BashExitCodeCheck
	sw.ScriptSections = s.ScriptSections
	sw.UseLegacyBashEval = s.UseLegacyBashEval

	script := sw.Build(s.Script)
	if err := shell(ctx, e, script, s.Step); err != nil {
		// AllowFailure is intentionally not honored: abstract shell
		// ignores it on script steps too, and the runner core has no
		// way to record a non-zero exit while clearing failure_reason.
		return fmt.Errorf("step %s: %w", s.Step, err)
	}

	return nil
}
```

**File:** functions/concrete/run/stages/step.go (L86-108)
```go
	var cmd string
	var args []string

	switch {
	case isPwsh:
		cmd = e.Shell
		args = []string{"-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", f.Name()}
	case e.LoginShell:
		cmd = e.Shell
		args = []string{"-l", f.Name()}
	default:
		cmd = f.Name()
	}

	// any user scripts that would previously be executed in the helper
	// container benefit from being able to use the bundled git and CA certs
	var envVars map[string]string
	switch stepName {
	case "pre_clone_script", "post_clone_script":
		envVars = e.HelperEnvs(envVars)
	}

	return e.Command(ctx, cmd, envVars, args...)
```

**File:** functions/concrete/run/env/env.go (L145-164)
```go
func (e *Env) Command(ctx context.Context, name string, env map[string]string, args ...string) error {
	environ := os.Environ()
	for k, v := range e.Env {
		environ = append(environ, k+"="+v)
	}
	for k, v := range e.GitLabEnv {
		environ = append(environ, k+"="+v)
	}
	for k, v := range env {
		environ = append(environ, k+"="+v)
	}

	cmd := gracefulexitcmd.New(ctx, e.GracefulExitDelay, name, args...)
	cmd.Dir = e.WorkingDir
	cmd.Env = environ
	cmd.Stdout = e.Stdout
	cmd.Stderr = e.Stderr

	return normalizeExitError(cmd.Run(), cmd.ProcessState)
}
```

**File:** functions/concrete/run/env/env.go (L166-184)
```go
// normalizeExitError reclassifies two exec outcomes that gracefulexitcmd
// surfaces as errors but which the runner's legacy bash-pipe execution
// (shells/bash.go on the docker executor) effectively treats as success:
//
//  1. The script exited 0, but a backgrounded child outlived
//     gracefulexitcmd's WaitDelay holding the parent's stdio pipes
//     open. WaitDelay's job is to bound that drain, not to fail the
//     job; the exit code already says the user script was fine.
//
//  2. The script's outer shell was terminated by a non-fatal
//     user-defined signal (SIGUSR1, SIGUSR2, SIGHUP, SIGPIPE). These
//     are routinely raised by user scripts that signal themselves
//     (e.g. `kill -USR1 $$`) and the bash pipeline wrapping in
//     functions/concrete/run/stages/internal/scriptwriter delivers
//     the signal to the outer shell rather than the subshell that
//     installs the trap, so what looks like a "script failure" here
//     is actually expected behaviour. Surfacing these as failures
//     diverges from the legacy executor without offering a recovery
//     path inside the user's script.
```

**File:** functions/concrete/run/env/env.go (L186-196)
```go
// Cancellation-driven SIGTERM (from gracefulexitcmd.Cmd.Cancel) is
// deliberately NOT included: the runner needs that to propagate so
// the build is reported as canceled rather than passed silently.
func normalizeExitError(err error, ps *os.ProcessState) error {
	if err == nil {
		return nil
	}

	if errors.Is(err, exec.ErrWaitDelay) && ps != nil && ps.ExitCode() == 0 {
		return nil
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
