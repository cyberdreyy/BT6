### Title
Unauthenticated gRPC `InitGracefulShutdown` allows arbitrary SSRF callback registration - ([File: helpers/runner_wrapper/wrapper.go])

### Summary
The `runner wrapper` gRPC service (`commands/wrapper.go`, `helpers/runner_wrapper/api/server/server.go`) exposes `InitGracefulShutdown` with no authentication, TLS, or URL validation. Any caller able to reach the listener (default `tcp://localhost:7777`, no credentials) can register a `ShutdownCallbackDef` with an attacker-chosen URL/method/headers, which is later executed via `http.DefaultClient.Do` from the runner host once the wrapped process exits.

### Finding Description
`RunnerWrapperCommand.Execute` starts a plain, unauthenticated gRPC server (`grpc.NewServer()`, no interceptors, `insecure.NewCredentials()` used on the client side too) bound by default to `tcp://localhost:7777` [1](#0-0) [2](#0-1) . `Server.InitGracefulShutdown` takes the caller-supplied `Url`, `Method`, and `Headers` straight from the protobuf request and builds a `ShutdownCallbackDef` with zero validation [3](#0-2) . `Wrapper.InitiateGracefulShutdown` stores this as `w.shutdownCallback` if `URL() != ""` [4](#0-3) . When the wrapped process later exits, `handleWrappedProcessShutdown` asynchronously invokes `sendShutdownCallback`, which calls `ShutdownCallback.Run` [5](#0-4) . `defaultShutdownCallback.Run` issues the HTTP request with `http.DefaultClient.Do`, sending attacker-supplied headers to an attacker-supplied URL, with no allow/deny-list, no restriction on internal/metadata addresses (e.g. `169.254.169.254`), and no check on caller identity [6](#0-5) .

However, the reachability precondition matters. This gRPC service does not wrap an individual CI/CD job — `RunnerWrapperCommand` wraps a "multi runner service" (the `gitlab-runner` binary/manager itself, per the command description "start multi runner service wrapped with gRPC manager server") [7](#0-6) . It is intended as an operator/supervisor control plane (e.g., for orchestrated graceful restarts/upgrades), not a per-job sandbox API. For a job to reach `127.0.0.1:7777`, it would need to share the host's loopback network namespace with the runner-wrapper process — true only for shell/host-based executors, not for container/VM-isolated executors (Docker, Kubernetes, etc.) where jobs run in separate network namespaces and cannot reach the runner host's loopback interface by default.

### Impact Explanation
Where reachable (effectively: shell executor or any executor sharing the host network namespace with a runner-wrapper-managed process), an unauthenticated caller can force the runner host to make an outbound HTTP request to an attacker-chosen destination (including internal services or cloud metadata endpoints) with attacker-chosen headers, persisting after any triggering job/session ends, and independent of who the caller is. Since the wrapped process is shared across all jobs handled by that runner-wrapper instance, the shutdown itself (and thus the callback firing) affects the whole manager's lifecycle, not a single job's sandbox — this is an SSRF-style pivot from the runner host, not from inside the job's own sandbox.

### Likelihood Explanation
Feasibility is bound entirely by network reachability of the gRPC listener. The listener defaults to TCP with no TLS/auth, so any process on the host (or any network peer, if bound non-locally or port-forwarded) can call it. For an unprivileged pipeline job specifically, exploitation is only realistic on shell/host-executors or any misconfiguration exposing the loopback/TCP port to job network namespaces — this is a real but executor-dependent precondition, not guaranteed by default GitLab Runner deployments (Docker/Kubernetes executors isolate job network namespaces from the host).

### Recommendation
1. Require authentication (e.g., a shared secret/token per session, or mTLS) on the `ProcessWrapper` gRPC service, and validate it in a `grpc.UnaryServerInterceptor` before dispatching to `InitGracefulShutdown`/`InitForcefulShutdown`.
2. Validate/restrict the `ShutdownCallbackDef` URL (scheme allow-list, block link-local/metadata/private ranges unless explicitly configured) before storing or executing it in `defaultShutdownCallback.Run`.
3. Default `--grpc-listen` to a `unix://` socket with restrictive file permissions instead of `tcp://localhost:7777`, and document that the wrapper's control plane must not be exposed to job execution environments.

### Proof of Concept
Go unit test in `helpers/runner_wrapper`:
```go
func TestSSRFViaShutdownCallback(t *testing.T) {
    // stand up httptest.Server as "attacker" listener, capturing headers/URL hit
    // build a Wrapper with a fake process/commander
    // call w.InitiateGracefulShutdown(api.NewInitGracefulShutdownRequest(
    //     api.NewShutdownCallbackDef(attackerServer.URL, "POST", map[string]string{"X-Exfil":"secret"})))
    // simulate wrapped process exit -> w.handleWrappedProcessShutdown(ctx, nil)
    // assert attackerServer received the request with expected method/headers,
    // with no authentication token supplied to InitiateGracefulShutdown
}
```
Expected assertions: the callback fires regardless of caller identity, and the destination/headers are fully attacker-controlled with no validation performed by `defaultShutdownCallback.Run`.

### Citations

**File:** commands/wrapper.go (L22-24)
```go
const (
	defaultWrapperGRPCListen = "tcp://localhost:7777"
)
```

**File:** commands/wrapper.go (L43-56)
```go
type RunnerWrapperCommand struct {
	GRPCListen                string        `long:"grpc-listen"`
	ProcessTerminationTimeout time.Duration `long:"process-termination-timeout"`
}

func NewRunnerWrapperCommand() cli.Command {
	return common.NewCommand(
		"wrapper", "start multi runner service wrapped with gRPC manager server",
		&RunnerWrapperCommand{
			GRPCListen:                defaultWrapperGRPCListen,
			ProcessTerminationTimeout: runner_wrapper.DefaultTerminationTimeout,
		},
	)
}
```

**File:** helpers/runner_wrapper/api/server/server.go (L30-36)
```go
func New(log logrus.FieldLogger, wrapper wrapper) *Server {
	return &Server{
		log:        log,
		wrapper:    wrapper,
		grpcServer: grpc.NewServer(),
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

**File:** helpers/runner_wrapper/api/shutdown_callback.go (L86-116)
```go
func (s *defaultShutdownCallback) Run(ctx context.Context) {
	s.log.Info("Running shutdown callback call")

	tctx, cancelFn := context.WithTimeout(ctx, defaultShutdownCallbackTimeout)
	defer cancelFn()

	req, err := http.NewRequestWithContext(tctx, s.method, s.url, nil)
	if err != nil {
		s.log.WithError(err).Error("Could not create shutdown callback request")
		return
	}

	for k, v := range s.headers {
		req.Header.Add(k, v)
	}

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		s.log.WithError(err).Error("Shutdown callback request failure")
		return
	}

	defer func() {
		_ = resp.Body.Close()
	}()
	_, _ = io.Copy(io.Discard, resp.Body)

	s.log.
		WithField("status-code", resp.StatusCode).
		WithField("status", resp.Status).
		Info("Received shutdown callback response")
```
