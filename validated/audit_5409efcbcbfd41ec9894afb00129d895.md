### Title
Step-runner requests are keyed by the non-secret, sequential CI_JOB_ID, breaking per-job isolation on the shared shell-executor socket - (File: executors/shell/steps.go)

### Summary
The shell executor multiplexes all concurrently running jobs' `run:` steps through a single shared `localserver.Server` unix socket, relying on step-runner's "per-job request id" as the only isolation mechanism between tenants. That request id is simply the job's plain, sequential `CI_JOB_ID`, which is not a secret and is guessable/enumerable by any GitLab user, letting a concurrent job query another job's step-runner status/log stream over the shared socket.

### Finding Description
`stepRunnerServer.ensureStarted` starts (or reuses) exactly one `localserver.Server` for the whole runner process, and every shell job's `executor.Connect` dials the same `sockPath` [1](#0-0) . The type's own comment states isolation between concurrently running builds is "by step-runner's per-job request id, not by separate processes" [2](#0-1) .

The request id used as that isolation key is produced in `NewRequest`:
```go
return &client.RunRequest{
    Id: strconv.FormatInt(jobInfo.ID, 10),
    ...
    Variables: addVariables(jobInfo.Variables), // includes masked vars / CI_JOB_TOKEN
}, nil
``` [3](#0-2) 

`jobInfo.ID` is the plain numeric GitLab CI job ID — a value that is not secret (visible in job URLs/API to the job's own author) and is sequential/instance-global, so IDs of jobs scheduled close together in time are close together numerically and thus guessable by an unrelated concurrent job on the same shared runner.

The client-side API surface (`Execute`, `runUserSteps`) only ever uses this bare `Id` to address a job for `Cancel`, `Status`, and `RunAndFollow`/`FollowLogs` calls [4](#0-3) [5](#0-4) , with no additional per-connection or per-tenant authentication token distinguishing which job/project issued the original request. The mock server used in this repo's own tests confirms the contract: `Status`/`Cancel`/`FollowLogs` handlers key purely off `req.Id` with no ownership check [6](#0-5) . No existing check in `executors/shell/steps.go`, `steps/steps.go`, or `steps/execute.go` binds a request id to the unix connection that created it, and no secret/random component is mixed into the id.

### Impact Explanation
If the real step-runner gRPC service (external dependency `gitlab.com/gitlab-org/step-runner`) follows the same id-only addressing shown by the client API and the test double, a second job sharing the same runner host and socket can issue a `Status`/`FollowLogs`/`Cancel` request using a guessed/derived `Id` matching another concurrently running job, and receive that job's step status, streamed step output, or cancel its run. Because `RunRequest.Variables` carries the job's variables including masked values and `CI_JOB_TOKEN` passed to `run:` steps [7](#0-6) , a successful cross-id query would leak another project's job secrets/token through the step-runner output stream — exactly the scoped impact (cross-project secret/token exfiltration).

### Likelihood Explanation
Preconditions match realistic shared-runner usage: multiple projects/jobs scheduled concurrently on one shell runner, all funneled through the single `ensureStarted()` server. The addressing value is not a secret (a sequential job ID), so an attacker job running at roughly the same time can brute-force or narrow down nearby IDs with high success probability, especially against a runner dedicated to a small number of projects. The main uncertainty is server-side: the actual RPC authorization/isolation logic lives inside the external `step-runner` binary/library, not in this repository, so it cannot be fully confirmed from this codebase whether that service adds any additional binding between a connection and its request id beyond what the client contract and test double expose.

### Recommendation
Do not use the plain, predictable `CI_JOB_ID` as the sole addressing/isolation key over a socket shared by multiple untrusted tenants. Generate a per-job, cryptographically random, unguessable token (e.g., UUIDv4) for the step-runner request `Id` in `steps/steps.go:NewRequest`, and/or have the shared `localserver.Server` bind each accepted connection to the request id it created (rejecting `Status`/`FollowLogs`/`Cancel` calls for ids not originated on that connection), closing the gap the current architecture comment already acknowledges.

### Proof of Concept
Go integration test against `steps/stepstest` (or a small harness around `localserver.Start`):
1. Start one shared server via `localserver.Start`.
2. Job A: build a `client.RunRequest` with `Id="1000"` and a masked `Variables` entry simulating `CI_JOB_TOKEN`; call `RunAndFollow` in a goroutine, capturing output into `bufA`.
3. Job B (attacker): immediately dial the same socket and call `Status(ctx, &StatusRequest{Id: "1000"})` and/or `FollowLogs`/`FollowOutput` with the same guessed `Id="1000"` before job A's stream completes.
4. Assert that job B's response contains job A's status/log content (including the masked variable value), proving cross-job delivery.
5. Repeat with `Id` values close to but not equal to a known job id to demonstrate guessability given sequential job IDs.

Expected assertion: job B must receive `codes.NotFound`/`PermissionDenied` or empty result for an id it did not create; observing job A's data instead confirms the isolation break.

### Citations

**File:** executors/shell/steps.go (L44-61)
```go
func (s *executor) Connect(_ context.Context) (func() (io.ReadWriteCloser, error), error) {
	if s.stepRunner == nil {
		// Only an executor built outside NewProvider reaches this.
		return nil, fmt.Errorf("shell executor was not constructed with a step-runner")
	}

	sockPath, err := s.stepRunner.ensureStarted()
	if err != nil {
		return nil, fmt.Errorf("starting step-runner: %w", err)
	}

	return func() (io.ReadWriteCloser, error) {
		conn, err := net.Dial("unix", sockPath)
		if err != nil {
			return nil, fmt.Errorf("dialing step-runner socket %q: %w", sockPath, err)
		}
		return conn, nil
	}, nil
```

**File:** executors/shell/steps.go (L64-66)
```go
// stepRunnerServer is the lifecycle wrapper around the in-process step-runner
// (steps/localserver) shared by every shell build. Concurrent builds are
// isolated by step-runner's per-job request id, not by separate processes.
```

**File:** steps/steps.go (L25-39)
```go
func NewRequest(jobInfo JobInfo, steps []schema.Step) (*client.RunRequest, error) {
	preambleSteps, err := addStepsPreamble(steps)
	if err != nil {
		return nil, fmt.Errorf("parsing step request: %w", err)
	}

	return &client.RunRequest{
		Id:        strconv.FormatInt(jobInfo.ID, 10),
		Timeout:   &jobInfo.Timeout,
		WorkDir:   jobInfo.ProjectDir,
		BuildDir:  jobInfo.ProjectDir,
		Env:       map[string]string{},
		Steps:     preambleSteps,
		Variables: addVariables(jobInfo.Variables),
	}, nil
```

**File:** steps/steps.go (L47-61)
```go
func addVariables(vars spec.Variables) []client.Variable {
	result := make([]client.Variable, 0, len(vars))
	for _, v := range vars {
		if variablesToOmit[v.Key] {
			continue
		}

		result = append(result, client.Variable{
			Key:    v.Key,
			Value:  v.Value,
			File:   v.File,
			Masked: v.Masked,
		})
	}
	return result
```

**File:** steps/execute.go (L104-115)
```go
	if opts.RegisterCancel != nil {
		opts.RegisterCancel(func() {
			// this context is for the Cancel request only.
			cancelCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if err := c.Cancel(cancelCtx, request.Id); err != nil {
				opts.Log.WithError(err).Warn("Failed to cancel step-runner job")
			}
		})
	}

	status, err := c.RunAndFollow(ctx, request, &out)
```

**File:** functions/concrete/run/run_steps.go (L59-70)
```go
	dialer := unixSocketDialer(socketPath())
	cli, err := extended.New(dialer)
	if err != nil {
		return fmt.Errorf("dialing step-runner: %w", err)
	}
	//nolint:errcheck
	defer cli.CloseConn()

	splitter := innerstream.New(r.env.Stdout, r.env.Stderr)
	out := &extended.FollowOutput{Logs: splitter}

	status, err := cli.RunAndFollow(ctx, req, out)
```

**File:** steps/stepstest/server.go (L98-133)
```go
// FollowLogs streams nothing and returns when the job is cancelled or the
// stream context is done — emulating step-runner shutting the log stream
// after a graceful cancel.
func (s *Server) FollowLogs(_ *proto.FollowLogsRequest, srv grpc.ServerStreamingServer[proto.FollowLogsResponse]) error {
	select {
	case <-s.cancelled:
		return nil
	case <-srv.Context().Done():
		return srv.Context().Err()
	}
}

// Status returns proto.StepResult_cancelled / StatusError_cancelled once the
// job has been cancelled, so that extended.RunAndFollow's post-Follow Status
// query produces the same result step-runner emits after a graceful cancel.
func (s *Server) Status(_ context.Context, req *proto.StatusRequest) (*proto.StatusResponse, error) {
	select {
	case <-s.cancelled:
		return &proto.StatusResponse{Jobs: []*proto.Status{{
			Id:        req.Id,
			Status:    proto.StepResult_cancelled,
			StartTime: timestamppb.Now(),
			EndTime:   timestamppb.Now(),
			Error: &proto.StatusError{
				Kind:    proto.StatusError_cancelled,
				Message: "cancelled",
			},
		}}}, nil
	default:
		return &proto.StatusResponse{Jobs: []*proto.Status{{
			Id:        req.Id,
			Status:    proto.StepResult_running,
			StartTime: timestamppb.Now(),
		}}}, nil
	}
}
```
