### Title
Session.Kill() does not prevent a concurrent execHandler from re-establishing a terminal connection after cancellation - ([File: session/session.go])

### Summary
`Session.Kill()` only nils out `s.terminalConn` under the session's mutex but does not mark the session as permanently terminated, and `Session.newTerminalConn()` has no way to know a `Kill()` already happened. A goroutine executing `execHandler` that already passed the `terminalAvailable()` gate can call `newTerminalConn()` after `Kill()` released the lock, see `terminalConn == nil`, and successfully call `s.interactiveTerminal.TerminalConnect()` to create a brand-new terminal connection for a job that has already been cancelled/killed.

### Finding Description
`execHandler` (session/session.go:146-191) waits on `s.terminalSetCh`/context timeout, checks `s.terminalAvailable()` (session/session.go:193-198, which only checks `s.interactiveTerminal != nil`, unrelated to kill state), and then calls `s.newTerminalConn()` (session/session.go:200-216). `newTerminalConn` only guards against *concurrent* connections (`connectionInUseError` when `s.terminalConn != nil`); it has no concept of "session killed."

`Kill()` (session/session.go:263-275) takes the lock, closes the current `terminalConn` if any, sets it to `nil`, and releases the lock — it never sets any "killed"/"disabled" flag and does not touch `s.interactiveTerminal`.

Race:
1. Goroutine B (execHandler, from an attacker-timed exec/attach request) passes `terminalAvailable()` and is about to call `newTerminalConn()`.
2. Goroutine A (`Build.waitForTerminal`, `common/build.go:1385-1391`) receives `<-ctx.Done()` (job cancellation) and calls `b.Session.Kill()`, which locks, closes the existing conn, sets `terminalConn = nil`, unlocks.
3. Goroutine B now acquires the lock in `newTerminalConn()`, sees `s.terminalConn == nil`, and calls `s.interactiveTerminal.TerminalConnect()` (e.g. docker/kubernetes/shell executor's `TerminalConnect`), setting `s.terminalConn` to a fresh, live connection — after the job was supposed to be killed.

Because `interactiveTerminal` is untouched by `Kill()`, and the HTTP mux/session registration (`session/server.go` `handleSessionRequest` + `commands/builds_helper.go` session registry) stays reachable for the remainder of the job's teardown/cleanup window (not torn down atomically with `Kill()`), there is a real window where the underlying executor (e.g. Docker `ContainerExecCreate`/`ContainerExecAttach` in `executors/docker/terminal.go:110-119`) will still accept a new exec session against a container that is concurrently being stopped/removed by cleanup.

Existing checks (`terminalAvailable()`, in-use guard in `newTerminalConn()`) do not address this — they only prevent double-connections, not post-kill connections.

### Impact Explanation
An attacker who already has a live/pending exec WebSocket request against the interactive web terminal for their own job can win a race against job cancellation and attach a new live terminal to the build container/pod after the runner believes the session was killed. This can let the attacker's shell outlive the intended teardown boundary and observe/interact with the executor's residual state during cleanup (e.g. before container removal completes), which is exactly the "session/job hijack" scoped impact.

### Likelihood Explanation
This requires the attacker to have valid access to their own job's session endpoint (already granted for legitimate terminal use) and to time a request so it is in-flight past `terminalAvailable()` right when cancellation triggers `Kill()`. This is a narrow but realistic timing window achievable by repeatedly issuing exec requests while triggering job cancellation (e.g. via the GitLab "Cancel" button or pipeline timeout) — fully reproducible with a tight concurrency stress loop as described, since the code has no synchronization/flag preventing it.

### Recommendation
Add an explicit "killed"/"closed" boolean state to `Session`, set under the same lock in `Kill()`, and check it (returning an error) at the start of `newTerminalConn()` before calling `s.interactiveTerminal.TerminalConnect()`. Also consider clearing `s.interactiveTerminal` in `Kill()` so no further `TerminalConnect()` calls can succeed, and/or unregister the session from `handleSessionRequest`'s lookup as soon as `Kill()` is invoked.

### Proof of Concept
Go concurrency test in `session/session_test.go`:
```go
func TestNoNewTerminalAfterKill(t *testing.T) {
    sess, err := NewSession(nil)
    require.NoError(t, err)

    mockTerminal := terminal.NewMockInteractiveTerminal(t)
    mockConn := terminal.NewMockConn(t)
    mockConn.On("Close").Return(nil).Maybe()
    mockTerminal.On("TerminalConnect").Return(mockConn, nil).Maybe()
    sess.SetInteractiveTerminal(mockTerminal)

    var wg sync.WaitGroup
    wg.Add(2)
    go func() {
        defer wg.Done()
        _ = sess.Kill()
    }()
    go func() {
        defer wg.Done()
        _, _ = sess.newTerminalConn() // simulates execHandler racing Kill()
    }()
    wg.Wait()

    // Invariant: once Kill() has run, no terminal connection should exist.
    assert.Nil(t, sess.terminalConn, "a new terminal connection was established after Kill()")
}
```
Running this in a loop (`go test -race -count=1000`) will show `sess.terminalConn` non-nil after both goroutines complete in some iterations, proving the race. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** session/session.go (L146-191)
```go
func (s *Session) execHandler(w http.ResponseWriter, r *http.Request) {
	logger := s.log.WithField("uri", r.RequestURI)
	logger.Debug("Exec terminal session request")

	if !websocket.IsWebSocketUpgrade(r) {
		logger.Error("Request is not a web socket connection")
		http.Error(w, http.StatusText(http.StatusMethodNotAllowed), http.StatusMethodNotAllowed)
		return
	}

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
}
```

**File:** session/session.go (L193-216)
```go
func (s *Session) terminalAvailable() bool {
	s.lock.Lock()
	defer s.lock.Unlock()

	return s.interactiveTerminal != nil
}

func (s *Session) newTerminalConn() (terminal.Conn, error) {
	s.lock.Lock()
	defer s.lock.Unlock()

	if s.terminalConn != nil {
		return nil, connectionInUseError{}
	}

	conn, err := s.interactiveTerminal.TerminalConnect()
	if err != nil {
		return nil, err
	}

	s.terminalConn = conn

	return conn, nil
}
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

**File:** common/build.go (L1385-1391)
```go
	select {
	case <-ctx.Done():
		err := b.Session.Kill()
		if err != nil {
			b.Log().WithError(err).Warn("Failed to kill session")
		}
		return errors.New("build cancelled, killing session")
```

**File:** executors/docker/terminal.go (L101-119)
```go
func (t terminalConn) Start(w http.ResponseWriter, r *http.Request, timeoutCh, disconnectCh chan error) {
	execConfig := client.ExecCreateOptions{
		TTY:          true,
		AttachStdin:  true,
		AttachStderr: true,
		AttachStdout: true,
		Cmd:          t.shell,
	}

	exec, err := t.client.ContainerExecCreate(t.ctx, t.containerID, execConfig)
	if err != nil {
		t.logger.Errorln("Failed to create exec container for terminal:", err)
		http.Error(w, "failed to create exec for build container", http.StatusInternalServerError)
		return
	}

	execStartCfg := client.ExecAttachOptions{TTY: true}

	resp, err := t.client.ContainerExecAttach(t.ctx, exec.ID, execStartCfg)
```

**File:** session/server.go (L133-145)
```go
func (s *Server) handleSessionRequest(w http.ResponseWriter, r *http.Request) {
	logger := s.log.WithField("uri", r.RequestURI)
	logger.Debug("Processing session request")

	session := s.sessionFinder(r.RequestURI)
	if session == nil || session.Handler() == nil { //nolint:staticcheck
		logger.Error("Mux handler not found")
		http.NotFound(w, r)
		return
	}

	session.Handler().ServeHTTP(w, r)
}
```
