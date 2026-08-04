This confirms the docker executor's terminal.go is the only implementation that adds an extra writer to the `proxy.StopCh` channel beyond the single "stopper" slot the `NewStreamProxy(1)` / `NewWebSocketProxy(1)` / `NewFileDescriptorProxy(1)` constructors are sized for (as evidenced by the identical "one stopper: terminal exit handler" comment across `executors/kubernetes/terminal.go` and `executors/shell/shell_terminal.go`, which only ever have `session/terminal/terminal.go`'s internal exit-handler goroutine write to that channel on timeout). The `gitlab.com/gitlab-org/gitlab-terminal` package itself is an external dependency (confirmed via `go.mod`/`go.sum`) and is not vendored in this repository, so I cannot verify its internal `GetStopCh`/`ProxyStream` read semantics directly from source.

### Title
Goroutine leak from double-send on `proxy.StopCh` when container-exit and terminal-timeout race - ([File: executors/docker/terminal.go])

### Summary
In `executors/docker/terminal.go`, `terminalConn.Start` spawns a goroutine that blocks on `t.executor.waiter.Wait(t.ctx, t.containerID)` and then sends a single error into `proxy.GetStopCh()`. This channel is the same `proxy.StopCh` passed into `terminalsession.ProxyTerminal`, whose own internal exit-handler goroutine can also send into it when `timeoutCh` fires. The proxy is constructed with `terminal.NewStreamProxy(1)` — capacity for exactly one "stopper" — so if both the container-exit goroutine and the timeout goroutine attempt to send around the same time, one send succeeds and the other blocks forever, since the stream proxy only consumes the stop signal once before tearing down.

### Finding Description
`terminalConn.Start` at [1](#0-0)  creates `proxy := terminal.NewStreamProxy(1)` (buffer sized for one stop signal, per the "one stopper: terminal exit handler" comment reused identically in `executors/kubernetes/terminal.go` and `executors/shell/shell_terminal.go`) and launches a goroutine that unconditionally does `stopCh <- err` once `waiter.Wait` returns.

That same `proxy.StopCh` is passed into `terminalsession.ProxyTerminal` at [2](#0-1) , whose own internal "terminal exit handler" goroutine (defined at [3](#0-2) ) also writes to `proxyStopCh <- err` when `timeoutCh` fires (from `Build.waitForTerminal`'s `b.Session.TimeoutCh <- err` path in [4](#0-3) ).

Unlike the kubernetes and shell executors — where the single buffered slot is only ever filled by `ProxyTerminal`'s own exit-handler goroutine — the docker executor introduces a *second, independent* writer to the same buffered channel. Since the buffer capacity is exactly 1 and the underlying `terminal.ProxyStream` (external `gitlab-terminal` package, not vendored in this repo) is expected to consume the stop signal only once before returning, if both writers attempt to send at nearly the same moment (build container exits right as the session hits `MaxSessionTime`/timeout), one send fills the buffer and returns, while the second send blocks indefinitely because nothing reads from that channel again after the proxy has already stopped.

### Impact Explanation
The blocked sender is a goroutine that never returns, permanently pinning its stack and any captured resources (closures over `t.logger`, `t.executor`, container ID, docker client) for the lifetime of the runner process. A job author who repeatedly opens interactive web terminal sessions and can influence container exit timing relative to the session's `MaxSessionTime` timeout can trigger this race repeatedly, leaking one goroutine per successful race and gradually exhausting runner-host resources — this is scoped exactly to "runner-host resource exhaustion / persistent disruption triggerable by an unprivileged job repeatedly opening/closing terminals."

### Likelihood Explanation
The race requires precise timing: the build container process must exit (or be killed) at essentially the same instant the terminal session's timeout fires. A job author fully controls their build script and can deliberately terminate the main container process, and can time this against a known/observable `MaxSessionTime`. This is a narrow timing window per attempt, so a single exploitation attempt has a modest success probability, but this is trivially retriable — an attacker opening many terminal sessions in a loop, each timed to race container exit against session timeout, can amplify the leak rate. The bug is deterministic in design (buffer size 1 with 2 unconditional writers) even though hitting the exact race window requires repeated attempts.

### Recommendation
Make the send in the docker executor's container-exit goroutine non-blocking (mirroring `nonBlockingSend` used elsewhere in `session/terminal/terminal.go`), e.g. use a `select` with a `default` case or check `t.ctx.Done()`/a dedicated "already stopped" signal before sending, so a losing writer never blocks. Alternatively, increase the stop channel capacity to account for both possible writers, or unify the two "stop" sources into a single arbitrated stop channel with only one writer.

### Proof of Concept
Go unit test in `executors/docker` (or a focused test against `terminalConn.Start`) that:
1. Mocks `t.executor.waiter.Wait` to return quickly (simulating container exit).
2. Simultaneously sends into `timeoutCh` right before/around when the waiter goroutine sends to `proxy.GetStopCh()`.
3. Runs both under `-race` with a bounded overall test timeout (e.g., `context.WithTimeout` + `runtime.NumGoroutine()` sampling before/after), asserting that goroutine count returns to baseline within a short grace period.
4. Repeats N times with randomized delays between the two trigger events to maximize the chance of hitting the exact race window, asserting no goroutine remains blocked on `stopCh <-` past the grace period (detectable via goroutine dump `pprof.Lookup("goroutine")` showing a stack blocked in `executors/docker/terminal.go`'s `Start.func1`).

### Citations

**File:** executors/docker/terminal.go (L126-141)
```go
	dockerTTY := newDockerTTY(&resp)
	proxy := terminal.NewStreamProxy(1) // one stopper: terminal exit handler

	// wait for container to exit
	go func() {
		t.logger.Debugln("Waiting for the terminal container:", t.containerID)
		err := t.executor.waiter.Wait(t.ctx, t.containerID)
		t.logger.Debugln("The terminal container:", t.containerID, "finished with:", err)

		stopCh := proxy.GetStopCh()
		if err != nil {
			stopCh <- fmt.Errorf("build container exited with %w", err)
		} else {
			stopCh <- errors.New("build container exited")
		}
	}()
```

**File:** executors/docker/terminal.go (L143-150)
```go
	terminalsession.ProxyTerminal(
		timeoutCh,
		disconnectCh,
		proxy.StopCh,
		func() {
			terminal.ProxyStream(w, r, dockerTTY, proxy)
		},
	)
```

**File:** session/terminal/terminal.go (L17-32)
```go
func ProxyTerminal(timeoutCh, disconnectCh, proxyStopCh chan error, proxyFunc func()) {
	disconnected := make(chan bool, 1)
	// terminal exit handler
	go func() {
		// wait for either session timeout or disconnection from the client
		select {
		case err := <-timeoutCh:
			proxyStopCh <- err
		case <-disconnected:
			// forward the disconnection event if there is any waiting receiver
			nonBlockingSend(
				disconnectCh,
				errors.New("finished proxying (client disconnected?)"),
			)
		}
	}()
```

**File:** common/build.go (L1392-1399)
```go
	case <-time.After(timeout):
		err := fmt.Errorf(
			"terminal session timed out (maximum time allowed - %s)",
			timeout.Round(time.Second),
		)
		b.logger.Infoln(err.Error())
		b.Session.TimeoutCh <- err
		return err
```
