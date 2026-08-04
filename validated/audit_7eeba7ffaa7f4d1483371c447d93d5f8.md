This confirms the vulnerability is real and matches the invariant that "job cancellation must terminate all descendants of the job process."

### Title
Detached/setsid descendants escape process-group kill signal, surviving job cancellation - (File: helpers/process/killer_unix.go)

### Summary
`unixKiller.Terminate` and `unixKiller.ForceKill` send `SIGTERM`/`SIGKILL` only to the process group of the job's direct child, obtained via `pk.getPID()` returning `-Pid` of the started command [1](#0-0) . A child process that calls `setsid(2)` or double-forks detaches itself from that process group, so it is not a member of `-Pid` anymore and does not receive either signal, letting it survive `KillAndWait`.

### Finding Description
The job process is started with `Setpgid: true` in `setProcessGroup`, making it the leader of a new process group equal to its own PID [2](#0-1) . `unixKiller.getPID` relies on POSIX `kill(2)` semantics where a negative PID targets every process whose *process group ID* equals `-pid` [1](#0-0) . Both `Terminate` (SIGTERM) and `ForceKill` (SIGKILL) use this same group-based delivery [3](#0-2) .

Any descendant of the job (spawned by the shell script or a custom-executor binary) that calls `setsid()` (or performs the classic double-fork daemonizing idiom) becomes the leader of a brand-new process group with a different PGID. Since kill-by-negative-PID only reaches processes whose PGID matches the target job's original PGID, the detached descendant is invisible to both `Terminate` and `ForceKill`. `osKillWait.KillAndWait` waits for `waitCh`, escalates from SIGTERM to SIGKILL after `gracefulKillTimeout`, and finally returns `*KillProcessError` once `forceKillTimeout` elapses without the parent process actually exiting cleanly on its own — none of which affects the detached descendant, which keeps running on the host [4](#0-3) .

No code path in `killer_unix.go`, `job_unix.go`, or `killer.go` inspects `/proc` (or an equivalent cgroup/pid-namespace mechanism) to discover and kill orphaned descendants outside the original process group; the only isolation primitive used is `Setpgid`, which is trivially defeated by `setsid(2)`.

### Impact Explanation
For the shell executor (and any custom executor spawning arbitrary binaries on a shared host), an unprivileged pipeline author can leave arbitrary long-running processes (CPU/network/disk-consuming) alive after their own job is cancelled, timed out, or otherwise terminated by the runner. On a shared/multi-tenant runner host, this consumes resources that should have been freed, potentially starving other concurrently scheduled jobs — matching the "Medium persistent multi-tenant disruption surviving cancellation" scope.

### Likelihood Explanation
This requires only a normal CI job with shell/script execution capability (no special privileges, no container escape) — `setsid` is a standard, unprivileged Linux utility, and detaching via `setsid` or a double fork is trivial to include in a job script. The runner does not sandbox job scripts against calling `setsid(2)`, so the technique is fully reachable and reliably repeatable on any shell/custom executor deployment lacking additional isolation (containers, cgroups, or PID namespaces are not enforced by this code path).

### Recommendation
Do not rely solely on process-group signalling. Track and kill the full descendant tree independently of PGID, e.g., by walking `/proc/*/status` for `PPid` chains rooted at the job PID (recursive reaping), or by placing the job in a dedicated cgroup (e.g., via `cgroup.kill` or freezing the cgroup before killing) and terminating everything in that cgroup regardless of session/process-group changes. As a lighter-weight complement, consider passing `Setsid: false`/blocking `setsid` isn't feasible from Go directly, so cgroup-based or PID-namespace-based containment is the practical fix.

### Proof of Concept
Add an integration test alongside `helpers/process/killer_unix_integration_test.go`:
1. Start a `Commander` whose command is a short shell script: `sh -c 'setsid sleep 999 & echo $!; wait'` (or a small helper binary that double-forks and execs `sleep 999999`), capturing the printed detached child PID.
2. Call `Terminate()` then `ForceKill()` on the `unixKiller` for that command, then wait for the parent via `waitCh`/`Wait()`.
3. Assert: (a) the parent process/`Commander` exits (e.g., `KillAndWait` returns normally or with `KillProcessError` only for the original PID), and (b) `syscall.Kill(detachedPID, 0)` still returns `nil` (no `ESRCH`), proving the `setsid`-detached descendant is still alive after both signal escalations complete.

### Citations

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

**File:** helpers/process/job_unix.go (L36-40)
```go
func setProcessGroup(c *exec.Cmd) {
	c.SysProcAttr = &syscall.SysProcAttr{
		Setpgid: true,
	}
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
