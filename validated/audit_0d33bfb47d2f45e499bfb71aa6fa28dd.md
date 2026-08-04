### Title
`Session.Kill()` fails to unblock the blocking `terminalConn.Start()` call for the Kubernetes and Docker executors, leaking goroutines and connections past job cancellation - ([File: session/session.go])

### Summary
`Session.execHandler` calls `terminalConn.Start(w, r, s.TimeoutCh, s.DisconnectCh)` synchronously and never passes any cancellable context into it, so the only way to terminate a blocked terminal session from the outside is `Session.Kill()` calling `terminalConn.Close()`. For the Kubernetes executor `Close()` is a complete no-op, and for the Docker executor `Close()` only cancels the context used to *set up* the exec (not the already-attached hijacked stream), so in both cases an attacker who opens `/exec` and never sends data or closes the connection keeps the handler goroutine (and its underlying sockets) alive indefinitely after job cancellation.

### Finding Description
`Session.execHandler` (`session/session.go:146-191`) creates a 1-minute context only to wait for `s.terminalSetCh` before the terminal connection exists (`session/session.go:156-167`); that context is discarded afterward and is never handed to `terminalConn.Start()` [1](#0-0) . `Start()`'s only defined interface is `Start(w, r, timeoutCh, disconnectCh)` with no `context.Context` parameter [2](#0-1) , and internally it blocks inside `ProxyTerminal`, which only unblocks when `timeoutCh` fires, the client disconnects (I/O error), or the underlying container/process exits [3](#0-2) .

`Session.Kill()` is the cancellation entry point: it only calls `s.terminalConn.Close()` and nils out the field — it never sends anything on `TimeoutCh`/`DisconnectCh` [4](#0-3) . Whether this actually stops the blocked `Start()` goroutine depends entirely on the executor's `Close()` implementation:

- Kubernetes: `Close()` is a literal no-op — `return nil` [5](#0-4) . Calling `Kill()` does nothing to interrupt the blocked `terminal.ProxyWebSocket(w, r, t.settings, wsProxy)` call.
- Docker: `Close()` only calls `t.cancelFn()`, which cancels the `context.Context` used to create/attach the exec session, but the actual data proxy loop reads/writes through `dockerTTY`, which wraps the already-hijacked `client.HijackedResponse` directly and is untouched by cancelling that context [6](#0-5) [7](#0-6) .
- Shell (contrast): `Close()` closes the pty file descriptor, which does actually interrupt the blocked read/write, so this executor is not affected the same way [8](#0-7) .

So for Kubernetes (always) and Docker (in the common case where the hijacked stream itself isn't independently closed by something else), cancelling a job via `Kill()` sets `s.terminalConn = nil` at the `Session` bookkeeping level (`Connected()` reports false), while the goroutine actually executing `execHandler -> terminalConn.Start()` keeps running, still holding the HTTP/websocket connection to the attacker's client, and (for k8s) the outbound exec websocket to the API server / (for docker) the hijacked docker exec socket.

### Impact Explanation
Each job that opens `/exec` and is then cancelled without closing its side of the websocket leaves behind one leaked goroutine plus the associated file descriptors/sockets (docker exec hijacked connection or k8s API exec websocket) that are never released. Because this is entirely attacker-controlled (any unprivileged pipeline author can open a web terminal on their own job and cancel it while holding the connection open), this leak accumulates per cancelled job across the whole runner process, degrading it for unrelated projects' jobs (goroutine/FD exhaustion), matching the described scoped impact.

### Likelihood Explanation
Preconditions are realistic and require no privilege beyond normal CI user permissions: the interactive web terminal feature must be enabled, the job must use the Docker or Kubernetes executor, and the user must open `/exec` and then trigger job cancellation (e.g. cancel from the UI/API) while keeping the client-side websocket connection open and idle. No forcible close of the underlying HTTP connection by the executor or GitLab Rails is required for the leak to occur, since `Kill()`'s only defense (`Close()`) is ineffective for these two executors. This is repeatable per job and does not depend on timing races beyond "cancel while the socket is open."

### Recommendation
Make `Session.Kill()` deterministically release the blocked `Start()` call regardless of executor: pass a cancellable `context.Context` (or the request's own context) into `terminal.Conn.Start()` and have each `ProxyTerminal`/proxy implementation select on it, and/or have `Kill()` forcibly close the underlying HTTP response writer's connection (e.g. via a `http.Hijacker`/`CloseNotifier` style shutdown) in addition to calling `terminalConn.Close()`. Additionally, fix the Kubernetes `Close()` to actually cancel/stop the k8s exec websocket (currently a no-op), and fix the Docker `Close()` to close the hijacked response (`dockerTTY.Close()` / `resp.Close()`) rather than only cancelling the setup context.

### Proof of Concept
Go integration test outline:
1. Start a `Session` with a mock/real Kubernetes (or Docker) `InteractiveTerminal` whose `TerminalConnect().Start()` blocks reading from `r`/`w` indefinitely unless the underlying connection errors.
2. Dial the `/exec` websocket endpoint as the client but never send/close.
3. Record `runtime.NumGoroutine()` baseline, then call `session.Kill()` to simulate job cancellation.
4. Assert that within a bounded time (e.g. 5s) `runtime.NumGoroutine()` returns to baseline and `session.Connected()`/the handler goroutine has actually exited — for Kubernetes/Docker mocks this assertion currently fails because the `Start()` goroutine is still blocked despite `Kill()` having been called, confirming the leak.

### Citations

**File:** session/session.go (L156-190)
```go
	ctx, cancel := context.WithTimeout(r.Context(), time.Minute)
	defer cancel()

	// There's a chance we'll get a interactive terminal connection before
	// we've hooked up the terminal to the underlying executor.
	//
	// When this occurs, we effectively wait for the terminal to be hooked up,
	// the request to be cancelled, or 1 minute (whichever comes first).
	select {
	case <-s.terminalSetCh:
	case <-ctx.Done():
	}

	if !s.terminalAvailable() {
		logger.Error("Interactive terminal not set")
		http.Error(w, http.StatusText(http.StatusServiceUnavailable), http.StatusServiceUnavailable)
		return
	}

	terminalConn, err := s.newTerminalConn()
	if _, ok := err.(connectionInUseError); ok {
		logger.Warn("Terminal already connected, revoking connection")
		http.Error(w, http.StatusText(http.StatusLocked), http.StatusLocked)
		return
	}

	if err != nil {
		logger.WithError(err).Error("Failed to connect to terminal")
		http.Error(w, http.StatusText(http.StatusInternalServerError), http.StatusInternalServerError)
		return
	}

	defer s.closeTerminalConn(terminalConn)
	logger.Debugln("Starting terminal session")
	terminalConn.Start(w, r, s.TimeoutCh, s.DisconnectCh)
```

**File:** session/session.go (L263-275)
```go
func (s *Session) Kill() error {
	s.lock.Lock()
	defer s.lock.Unlock()

	if s.terminalConn == nil {
		return nil
	}

	err := s.terminalConn.Close()
	s.terminalConn = nil

	return err
}
```

**File:** session/terminal/terminal.go (L12-15)
```go
type Conn interface {
	Start(w http.ResponseWriter, r *http.Request, timeoutCh, disconnectCh chan error)
	Close() error
}
```

**File:** session/terminal/terminal.go (L17-36)
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

	proxyFunc()
	disconnected <- true
}
```

**File:** executors/kubernetes/terminal.go (L41-43)
```go
func (t terminalConn) Close() error {
	return nil
}
```

**File:** executors/docker/terminal.go (L153-158)
```go
func (t terminalConn) Close() error {
	if t.cancelFn != nil {
		t.cancelFn()
	}
	return nil
}
```

**File:** executors/docker/tty.go (L15-21)
```go
func (d *dockerTTY) Read(p []byte) (int, error) {
	return d.hijackedResp.Reader.Read(p)
}

func (d *dockerTTY) Write(p []byte) (int, error) {
	return d.hijackedResp.Conn.Write(p)
}
```

**File:** executors/shell/shell_terminal.go (L34-36)
```go
func (t terminalConn) Close() error {
	return t.shellFd.Close()
}
```
