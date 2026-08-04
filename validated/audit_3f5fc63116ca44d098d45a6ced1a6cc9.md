### Title
Unauthenticated gRPC control plane allows any process reaching 127.0.0.1:7777 to force-kill the wrapped multi-runner process - ([File: helpers/runner_wrapper/api/server/server.go])

### Summary
The `gitlab-runner wrapper` subcommand starts a gRPC server (`server.Server`) with no TLS and no authentication, bound by default to `tcp://localhost:7777`, exposing `InitForcefulShutdown` which unconditionally calls `Wrapper.InitiateForcefulShutdown()` to kill the wrapped `gitlab-runner run` process. Any process in the same network namespace as the wrapper (e.g. a job running under an executor that shares the host's loopback namespace) can dial this port and terminate the entire multi-runner process, killing every concurrently running job of every project served by that runner instance.

### Finding Description
`RunnerWrapperCommand.Execute` creates a plaintext TCP listener via `createListener()` at the default address `tcp://localhost:7777` [1](#0-0) [2](#0-1) , then registers `server.New(grpcLog, w)` on it, which internally builds `grpc.NewServer()` with no `grpc.Creds` / TLS option and no interceptor performing authentication [3](#0-2) . The `ProcessWrapperServer` interface exposes `InitForcefulShutdown` as a plain unary RPC with an empty request message (`InitForcefulShutdownRequest {}`) requiring no token or credential [4](#0-3) . The handler `Server.InitForcefulShutdown` calls `s.wrapper.InitiateForcefulShutdown()` unconditionally [5](#0-4) , which in turn locates the single wrapped process and issues `w.forcefulShutdown(p)` followed by `setStatus(api.StatusInShutdown)` [6](#0-5) . There is exactly one wrapped process per wrapper instance — the multi-runner `gitlab-runner run` process that itself manages jobs from potentially many projects/runners — so killing it terminates all of them at once.

Any process that can open a TCP connection to `127.0.0.1:7777` (the default listen address) can perform: dial → `pb.NewProcessWrapperClient` → `InitForcefulShutdown` RPC → server handler → `Wrapper.InitiateForcefulShutdown` → kill signal to the wrapped process, with zero checks along that entire path. There is no allowlist, token, mTLS, or peer-credential check anywhere in `server.Server` or `commands.RunnerWrapperCommand`.

### Impact Explanation
Because the wrapper manages a single wrapped `gitlab-runner run` process that serves as the multi-runner/multi-tenant execution engine, a successful unauthenticated `InitForcefulShutdown` call kills that process and, by extension, every job currently executing under every runner/project configured on that host — not just the caller's own job. This is a cross-tenant denial of service that a normal job triggers deliberately or accidentally by simply being able to reach loopback port 7777, and it persists beyond the life of the attacking job (the parent process is dead; all sibling jobs fail).

### Likelihood Explanation
The precondition is that a job process can reach `127.0.0.1:7777` — true by default whenever a job's network path includes the host's loopback namespace (e.g., shell/ssh executors run directly on the host, or docker executors configured with host networking). No special executor misconfiguration beyond "job can reach localhost" is required, and the RPC call itself needs no credentials, so exploitation is a two-line gRPC client call. This is highly feasible and fully repeatable — the wrapper's gRPC service is unauthenticated by design (no auth code exists anywhere in `server.go`), not merely relying on an admin-chosen insecure setup like `docker.sock` exposure.

### Recommendation
Add authentication/authorization to the wrapper's gRPC control channel before accepting mutating RPCs like `InitForcefulShutdown`/`InitGracefulShutdown`: e.g., require a per-invocation shared secret/token generated at wrapper startup and passed via gRPC metadata, validated with a `grpc.UnaryServerInterceptor`; alternatively, restrict the listener to a Unix domain socket with filesystem permissions scoped to the runner process owner (the code already supports `unix://` in `createListener`), and disable the plaintext TCP default in multi-tenant deployments. At minimum, verify peer identity/credentials before invoking `s.wrapper.InitiateForcefulShutdown()` in `helpers/runner_wrapper/api/server/server.go`.

### Proof of Concept
Go integration test:
1. Start a real `Wrapper` (`runner_wrapper.New`) wrapping a long-running dummy process (e.g., `sleep 100`), call `w.Run(ctx)` in a goroutine.
2. Start `server.New(log, w)` and `srv.Listen(listener)` on a `net.Listen("tcp", "127.0.0.1:0")` address (simulating the default TCP listener with no auth).
3. From a separate, unprivileged goroutine/process context (no credentials, no token), dial the listener with `grpc.NewClient(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))`, build `pb.NewProcessWrapperClient(conn)`, call `InitForcefulShutdown(ctx, &pb.InitForcefulShutdownRequest{})`.
4. Assert the RPC succeeds (`err == nil`) without any authentication step being possible.
5. Assert `w.Status()` transitions to `api.StatusInShutdown` and that the dummy wrapped process actually receives a termination signal (process exits).
6. Optionally simulate "other tenant" jobs by having the wrapped dummy process represent a `gitlab-runner run` managing concurrent job goroutines, and assert they are all terminated as a side effect of step 5.

### Citations

**File:** commands/wrapper.go (L22-24)
```go
const (
	defaultWrapperGRPCListen = "tcp://localhost:7777"
)
```

**File:** commands/wrapper.go (L68-106)
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

**File:** helpers/runner_wrapper/api/proto/wrapper.proto (L35-45)
```text
message InitForcefulShutdownRequest {}

message InitForcefulShutdownResponse {
  Status status = 1;
  string failureReason = 2;
}

service ProcessWrapper {
  rpc CheckStatus(CheckStatusRequest) returns (CheckStatusResponse);
  rpc InitGracefulShutdown(InitGracefulShutdownRequest) returns (InitGracefulShutdownResponse);
  rpc InitForcefulShutdown(InitForcefulShutdownRequest) returns (InitForcefulShutdownResponse);
```

**File:** helpers/runner_wrapper/wrapper.go (L239-258)
```go
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
