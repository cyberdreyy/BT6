### Title
Job-spawned detached processes retain open read access to unlinked response.json / build_exit_code after Cleanup() calls os.RemoveAll, allowing post-job secret exfiltration - ([File: executors/custom/custom.go])

### Summary
`executor.Cleanup()` relies solely on `os.RemoveAll(e.tempDir)` to remove the job's temp directory containing `response.json` (holding the full `Build.Job` payload, including the job token/secrets) and `build_exit_code`. `command.command.Run()`'s cancellation path (`KillAndWait`) only manages the direct `cmd.Process` started via `exec.Cmd`, not any child/grandchild processes the job's own `RunExec` script may have spawned and detached (e.g., `tail -f $JOB_RESPONSE_FILE &`). On POSIX filesystems, `RemoveAll` merely unlinks directory entries; any process still holding an open file descriptor on `response.json` continues to have full read access to the inode's contents indefinitely.

### Finding Description
`e.createJobResponseFile()` writes the JSON-encoded `Build.Job` (containing the job token and other secrets) to `e.tempDir/response.json` [1](#0-0) . This path is exposed to the job script via the `JOB_RESPONSE_FILE` env var set in `command.New` [2](#0-1) , and the file is fully attacker(job)-readable content by design (that's the intended contract of the custom executor).

`executor.Run()` invokes the configured `RunExec` script with this environment and waits on `command.Run()` [3](#0-2) . Inside `command.Run()`, the only two paths are (a) `c.cmd.Wait()` completing normally, or (b) context cancellation triggering `KillAndWait(c.cmd, c.waitCh)`, which acts only on `c.cmd` (the direct process), not on a process group or any subprocess tree [4](#0-3) . Neither `Run()` nor `Cleanup()` sets up a process group (e.g. `Setpgid`) or performs a group kill; there is no code anywhere in `executor.Run`/`Cleanup`/`command.command` that discovers or terminates descendant processes forked and detached by the job script before it exits (e.g. via `nohup ... &` or double-fork).

`Cleanup()` then does: `defer func() { _ = os.RemoveAll(e.tempDir) }()` [5](#0-4) . `RemoveAll` unlinks directory entries; it does not (and cannot, from userspace on POSIX) revoke already-open file descriptors held by other processes. If the job's `RunExec` script started a detached child (`tail -f $JOB_RESPONSE_FILE &` or similar) before the script/`RunExec` process itself exits, that child retains an open fd on the response.json inode. Since the runner never tracks or kills such descendants, that background process can continue reading the secret content indefinitely after the job is reported complete and after `RemoveAll` has run.

Existing protections do not prevent this: file permissions (`0o600`) only gate access at open-time by uid, and the malicious job runs the reading process under the same job/agent uid that already had legitimate read access; masking/secret redaction applies to job logs/traces, not to files on disk; there is no `KillAndWait`/process-group-based cleanup enforcing that all job-descendant processes terminate before or during `RemoveAll`.

### Impact Explanation
A pipeline author (unprivileged, but the author of their own job's `RunExec`-invoked script) can cause secrets contained in `response.json` (which encodes `Build.Job`, including CI job token and potentially other secret-bearing fields serialized into the job payload) to remain readable by a background process they control, past job completion and past the runner's cleanup step. This breaks the invariant that "secrets exposed via JOB_RESPONSE_FILE must not be accessible after job completion/cleanup," since the process keeps the token available for later use (e.g., to call GitLab APIs with the job token) even though the runner believes the job/its artifacts have been cleaned up. This is a real logic gap, but the practical severity is bounded: the attacker is exfiltrating a secret from *their own job* to *their own long-lived process on the same host*, which they already had legitimate access to during the job's runtime — the "leak" is to themselves, not to another job or another user, unless that host is shared with other tenants (custom executor drivers targeting shared infra, e.g., SSH/VM drivers) where lingering job-owned processes could be a persistence/token-longevity concern.

### Likelihood Explanation
Feasible and fully within job-author control: it only requires the `RunExec` driver script (attacker-controlled to the extent it embeds/executes the job's own script content) to detach a child process holding `$JOB_RESPONSE_FILE` open before exiting, e.g. `tail -f "$JOB_RESPONSE_FILE" &`. No special driver privileges, race conditions, or GitLab-admin actions are needed — the required condition (RunExec forking a detached child that outlives the parent) is standard shell scripting. It is deterministically repeatable per job run.

### Recommendation
When killing/waiting on the job process in `command.command.Run`/`KillAndWait`, and in `executor.Cleanup`, start `RunExec` in its own process group (e.g. `Setpgid: true` in `SysProcAttr`) and, at the end of `Run()`/before `Cleanup()`'s `RemoveAll`, kill the entire process group (negative PID signal) rather than only `cmd.Process`, ensuring any detached descendants are terminated before/along with directory removal. Additionally, consider truncating/overwriting `response.json` content (not just unlinking) before or as part of cleanup so that even lingering open fds see zeroed data, and avoid keeping secret-bearing job payload in a long-lived plaintext file when possible (e.g., short-lived socket/pipe-based delivery instead of a file that persists on disk).

### Proof of Concept
Go integration test in `executors/custom` (extending existing `integration_test.go`):
1. Configure a custom executor with `RunExec` set to a shell script that: reads `$JOB_RESPONSE_FILE`, starts `tail -f "$JOB_RESPONSE_FILE" > /tmp/leaked_output &` (detached, disown), then exits normally.
2. Run the job to completion (`executor.Run()` returns nil), then call `executor.Cleanup()`.
3. Assert: (a) `e.tempDir` no longer exists via `os.Stat` (RemoveAll succeeded); (b) the detached `tail` process (tracked via its PID written to a known file) is still running (`syscall.Kill(pid, 0)` returns nil) after `Cleanup()` returns; (c) `/tmp/leaked_output` continues to receive/contain the job secret content (e.g., job token) appended after cleanup, proving the inode/content is still accessible via the lingering process despite `RemoveAll`.
4. Expected (current, vulnerable) behavior: all three assertions pass, showing no process-group kill occurs and the secret remains exfiltratable. A fix should make assertion (b) fail (process killed) after the recommended process-group-kill change is applied.

### Citations

**File:** executors/custom/custom.go (L162-177)
```go
func (e *executor) createJobResponseFile() (string, error) {
	responseFile := filepath.Join(e.tempDir, "response.json")
	file, err := os.OpenFile(responseFile, os.O_RDWR|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return "", fmt.Errorf("creating job response file %q: %w", responseFile, err)
	}
	defer func() { _ = file.Close() }()

	encoder := json.NewEncoder(file)
	err = encoder.Encode(e.Build.Job)
	if err != nil {
		return "", fmt.Errorf("encoding job response file: %w", err)
	}

	return responseFile, nil
}
```

**File:** executors/custom/custom.go (L318-356)
```go
func (e *executor) Run(cmd common.ExecutorCommand) error {
	scriptDir, err := os.MkdirTemp(e.tempDir, "script")
	if err != nil {
		return err
	}

	scriptName := "script"
	if e.BuildShell.Extension != "" {
		scriptName += "." + e.BuildShell.Extension
	}

	scriptFile := filepath.Join(scriptDir, scriptName)
	err = os.WriteFile(scriptFile, []byte(cmd.Script), 0o700)
	if err != nil {
		return err
	}

	// TODO: Remove this translation - https://gitlab.com/groups/gitlab-org/-/epics/6112
	stage := cmd.Stage
	if stage == "step_script" {
		e.BuildLogger.Warningln("Starting with version 17.0 the 'build_script' stage " +
			"will be replaced with 'step_script': https://gitlab.com/groups/gitlab-org/-/epics/6112")
		stage = "build_script"
	}

	args := append(e.config.RunArgs, scriptFile, string(stage)) //nolint:gocritic

	opts := prepareCommandOpts{
		executable: e.config.RunExec,
		args:       args,
		out: commandOutputs{
			stdout: e.BuildLogger.Stream(buildlogger.StreamWorkLevel, buildlogger.Stdout),
			stderr: e.BuildLogger.Stream(buildlogger.StreamWorkLevel, buildlogger.Stderr),
		},
	}
	defer opts.out.Close()

	return e.prepareCommand(cmd.Context, opts).Run()
}
```

**File:** executors/custom/custom.go (L358-396)
```go
func (e *executor) Cleanup() {
	e.AbstractExecutor.Cleanup()

	err := e.prepareConfig()
	if err != nil {
		e.BuildLogger.Warningln(err)

		// at this moment we don't care about the errors
		return
	}

	defer func() { _ = os.RemoveAll(e.tempDir) }()

	// nothing to do, as there's no cleanup_script
	if e.config.CleanupExec == "" {
		return
	}

	ctx, cancelFunc := context.WithTimeout(context.Background(), e.config.GetCleanupScriptTimeout())
	defer cancelFunc()

	stdoutLogger := e.BuildLogger.WithFields(logrus.Fields{"cleanup_std": "out"})
	stderrLogger := e.BuildLogger.WithFields(logrus.Fields{"cleanup_std": "err"})

	opts := prepareCommandOpts{
		executable: e.config.CleanupExec,
		args:       e.config.CleanupArgs,
		out: commandOutputs{
			stdout: stdoutLogger.WriterLevel(logrus.DebugLevel),
			stderr: stderrLogger.WriterLevel(logrus.WarnLevel),
		},
	}
	defer opts.out.Close()

	err = e.prepareCommand(ctx, opts).Run()
	if err != nil {
		e.BuildLogger.Warningln("Cleanup script failed:", err)
	}
}
```

**File:** executors/custom/command/command.go (L56-62)
```go
	defaultVariables := map[string]string{
		"TMPDIR":                          cmdOpts.Dir,
		api.BuildFailureExitCodeVariable:  strconv.Itoa(BuildFailureExitCode),
		api.SystemFailureExitCodeVariable: strconv.Itoa(SystemFailureExitCode),
		api.BuildCodeFileVariable:         options.BuildExitCodeFile,
		api.JobResponseFileVariable:       options.JobResponseFile,
	}
```

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
