The audit target is confirmed: `RunnerWrapperCommand.createListener` binds the default listener to `tcp://localhost:7777`, and `server.New`/`Server.Listen` register the gRPC service with a bare `grpc.NewServer()` — no TLS credentials, no auth interceptor, no token check anywhere in the RPC handlers.### Title
Unauthenticated gRPC wrapper-management listener allows any local process to force shutdown of the multi-runner control plane - ([File: commands/wrapper.go])

### Summary
`RunnerWrapperCommand.Execute` binds by default to `tcp://localhost:7777` via `createListener` and hands the listener to `server.New(...).Listen`, which registers `CheckStatus`, `InitGracefulShutdown`, and `InitForcefulShutdown` RPCs on a bare `grpc.NewServer()` with no TLS credentials, no interceptor, and no token/identity check. Any local process able to reach that loopback port can invoke these RPCs and force a graceful or immediate shutdown of the wrapped multi-runner process.

### Finding Description
`commands/wrapper.go` creates the listener and server: [1](#0-0) 
The default address is `tcp://localhost:7777`: [2](#0-1) 

The gRPC server itself is constructed with no auth mechanism, and every RPC handler executes unconditionally without checking caller identity: [3](#0-2) [4](#0-3) [5](#0-4) 

`InitGracefulShutdown` and `InitForcefulShutdown` directly reach the wrapped multi-runner OS process and signal it (SIGTERM for graceful, an OS-specific forceful signal), transitioning it to `StatusInShutdown`: [6](#0-5) 

The only "protection" in place is binding to the loopback interface (`127.0.0.1:7777`), confirmed by the test's expected address: [7](#0-6) 
Binding to loopback is not an authentication or authorization boundary — it only excludes remote network attackers, not other local users/processes on the same host. The client itself explicitly uses insecure transport credentials, corroborating that the whole channel is designed with no auth layer at all: [8](#0-7) 

### Impact Explanation
This "wrapper" command is a process-supervision helper: it launches a `gitlab-runner run` (multi-runner) process as a child and exposes gRPC controls to check its status or trigger graceful/forceful shutdown, presumably for use by orchestration layers (e.g., a supervisor/init process or Kubernetes-based deployment) that need to manage a runner manager's lifecycle. When the wrapper binds to plain TCP loopback with zero authentication, any unprivileged local process capable of reaching `127.0.0.1:7777` on the same host/network namespace — for example, a shell-executor CI job running directly on that host, or any process sharing that network namespace — can call `InitForcefulShutdown`/`InitGracefulShutdown` and terminate the entire wrapped multi-runner process. Because a single runner manager typically serves many concurrent jobs across multiple projects, this allows one project's job to disrupt or interrupt other projects' in-flight jobs (e.g., jobs mid-flight fetching `CI_JOB_TOKEN`-scoped resources), a denial-of-service/control-plane impact that crosses project isolation boundaries.

### Likelihood Explanation
Feasibility depends entirely on network reachability of the loopback listener from job execution context. This is concretely reachable when:
- The runner uses the shell/custom executor and jobs run as OS processes directly on the same host as the wrapper (no network namespace isolation) — in this deployment mode, any job script can simply run `nc localhost 7777` or a gRPC client and hit the endpoint.
- The runner deployment shares the host network namespace with job containers (e.g., `--network=host` Docker executor configurations, or Kubernetes pods sharing host networking).

It is not reachable from default-isolated container executors (bridge-networked Docker, standard Kubernetes pod networking) since those give jobs a separate loopback namespace from the host. The finding is valid wherever the wrapper and the job execution environment share a network namespace, which is a supported and documented deployment shape for this component, not an admin misconfiguration being excluded by the audit rules (it's a missing auth check in the RPC server itself, not a "privileged container" or "docker.sock" style admin-choice issue).

### Recommendation
Add per-RPC authentication/authorization to the wrapper gRPC server — e.g., a shared secret/token passed via gRPC metadata and validated in a `grpc.UnaryInterceptor`, or switch the default transport to a Unix domain socket with restrictive file permissions (already supported via the `unix://` scheme) instead of defaulting to plain TCP. At minimum, mutate the default away from unauthenticated TCP and document/enforce that TCP mode requires TLS + token auth before it is used in any environment where job workloads could share the host network namespace.

### Proof of Concept
```go
// helpers/runner_wrapper/api/server/server_authz_test.go
func TestServer_UnauthenticatedShutdown(t *testing.T) {
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	require.NoError(t, err)

	mockWrapper := &mockWrapperImpl{} // implements Status/FailureReason/InitiateGracefulShutdown/InitiateForcefulShutdown
	srv := server.New(logrus.New(), mockWrapper)
	go srv.Listen(lis)
	defer srv.Stop()

	// Simulate an unprivileged local process (e.g. a shell-executor job) with no credentials.
	conn, err := grpc.NewClient(lis.Addr().String(), grpc.WithTransportCredentials(insecure.NewCredentials()))
	require.NoError(t, err)
	client := pb.NewProcessWrapperClient(conn)

	// No auth metadata attached at all.
	resp, err := client.InitForcefulShutdown(context.Background(), &pb.InitForcefulShutdownRequest{})
	require.NoError(t, err) // succeeds -- should have been rejected with Unauthenticated
	assert.True(t, mockWrapper.forcefulShutdownCalled)
}
```
Expected (buggy) behavior: the RPC succeeds and `InitiateForcefulShutdown` is invoked with no identity check, proving any local, unauthenticated caller can shut down the wrapped multi-runner process.

### Citations

**File:** commands/wrapper.go (L22-24)
```go
const (
	defaultWrapperGRPCListen = "tcp://localhost:7777"
)
```

**File:** commands/wrapper.go (L68-90)
```go
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

**File:** helpers/runner_wrapper/api/server/server.go (L30-47)
```go
func New(log logrus.FieldLogger, wrapper wrapper) *Server {
	return &Server{
		log:        log,
		wrapper:    wrapper,
		grpcServer: grpc.NewServer(),
	}
}

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

**File:** commands/wrapper_test.go (L86-90)
```go
		"default address": {
			grpcAddress:     defaultWrapperGRPCListen,
			expectedNetwork: "tcp",
			expectedAddress: "127.0.0.1:7777",
		},
```

**File:** helpers/runner_wrapper/api/client/client.go (L37-38)
```go
	grpcOpts := []grpc.DialOption{
		grpc.WithTransportCredentials(insecure.NewCredentials()),
```
