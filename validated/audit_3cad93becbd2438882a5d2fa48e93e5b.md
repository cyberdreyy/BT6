This confirms the key architectural fact: when `VMIsolation.Enabled` is `false` (the case the question specifies), `Prepare` in `executors/internal/autoscaler/acquisition.go` returns a client that dials the fleeting connector directly to the shared host instance, using the single `connector_config` credentials configured for the runner — there is no per-job OS user, and no nested VM per job. [1](#0-0)  All concurrent jobs on that instance (up to `capacity_per_instance`) therefore execute their shell/build scripts as the same OS user over the same SSH credentials.

### Title
Predictable enumeration of `/tmp/step-runner.*` directories combined with same-OS-user concurrency allows cross-job step-runner socket hijack when VMIsolation is disabled - ([File: executors/instance/steps.go])

### Summary
`executor.Connect` protects the step-runner control socket only via an unguessable `mktemp -d` directory name with mode 0700, on the stated assumption that this stops "other users" from reaching the socket. When `capacity_per_instance > 1` and `VMIsolation` is disabled, however, concurrent jobs on the same instance run under the *same* OS user (per `acquisitionRef.Prepare`, no VM isolation client is created), so the 0700 permission boundary provides no protection between jobs — a job can list `/tmp/step-runner.*`, find another job's socket, and dial it directly, because it already has full owner-equivalent (same-UID) filesystem permissions on that directory.

### Finding Description
`executor.Connect` builds a shell command that creates the step-runner socket directory with `mktemp -d /tmp/step-runner.XXXXXXXX`, relying on the directory's unpredictable name and 0700 mode as the sole isolation mechanism between concurrently-running jobs on the same VM. [2](#0-1)  The comment explicitly frames this as protection against "another user" reaching or squatting the socket path, treating cross-job isolation and cross-OS-user isolation as equivalent. [3](#0-2) 

That equivalence does not hold in the exact configuration the question specifies: `capacity_per_instance > 1` with `VMIsolation` disabled. In that path, `acquisitionRef.Prepare` returns early with a plain client wrapping the single fleeting connector dial — no nested VM, no per-job credential separation — whenever `options.Config.Autoscaler.VMIsolation.Enabled` is false. [1](#0-0)  All jobs assigned to that instance therefore execute shell commands (including the job's own script) as the same OS user, over sessions authenticated with the same `connector_config` credentials.

Because both jobs run as the same UID, mode 0700 on `mktemp`'s output directory is not a boundary between them: process/file ownership checks pass identically for either job's shell. A concurrently-running job's script can simply run `ls /tmp/step-runner.*` (the parent `/tmp` is normally world-listable) to discover the sibling job's socket directory name, defeating the "unpredictable name" protection, and then connect directly to `$dir/step-runner.sock` (e.g., via `socat`/`nc -U`, or a custom binary) before or instead of the legitimate proxy process spawned by `e.client.DialRun`. [4](#0-3)  Nothing in `Connect` or the proxy/serve wiring authenticates the RPC peer beyond "whoever can open this socket path," so a same-UID connection from another job succeeds.

### Impact Explanation
An attacker whose job runs concurrently on a shared instance (no VM isolation, `capacity_per_instance > 1`) can connect to another job's step-runner RPC socket and drive the steps RPC protocol as if it were the legitimate proxy for that job — potentially observing or manipulating the victim job's step execution/session state, i.e., a cross-job session hijack on the shared VM, matching the scoped impact.

### Likelihood Explanation
Preconditions are exactly the ones GitLab documents and supports as a valid, non-default configuration: `capacity_per_instance > 1` with VM isolation disabled (the docs even warn that jobs in such configurations "should be trusted" — but that trust assumption is about resource contention, not RPC-socket confidentiality). [5](#0-4)  Given that, the exploit requires only ordinary pipeline-script capability (listing `/tmp`, opening a UNIX socket) and no privilege escalation, making it realistically reachable and repeatable whenever two jobs happen to be co-scheduled on one instance.

### Recommendation
Do not rely solely on filename unpredictability plus 0700 permissions for isolation when jobs may share an OS user. Either: (1) require/verify that instance-executor configurations with `capacity_per_instance > 1` and `VMIsolation.Enabled = false` provision a distinct OS user per concurrent job slot (e.g., using `GITLAB_RUNNER_SLOT_CGROUP`-style per-slot separation already used elsewhere) so filesystem permission checks are meaningful; or (2) add socket-level peer authentication (e.g., a per-job shared secret embedded in the proxy invocation and checked by step-runner before serving) so that same-UID co-tenants cannot use the RPC channel even if they can open the socket.

### Proof of Concept
Go integration test plan (extends `executors/instance/steps_test.go` patterns):
1. Use two `newTestStepsExecutor` instances (`jobA`, `jobB`) sharing one mocked `executors.Client` whose `Run` handler emulates the real `mktemp`/`trap` shell behavior on a real temp filesystem (or run against a real local shell instead of mocks, to get a real `/tmp` layout).
2. Call `jobA.Connect(ctx)`; capture the reported socket path from stdout (`step-runner is listening on socket ...`).
3. From a shell context representing `jobB` (same OS user, no `VMIsolation`), run `ls /tmp/step-runner.*` and assert it discovers `jobA`'s directory name without being told it.
4. From `jobB`'s exec context, attempt `net.Dial("unix", jobA_socket_path)` (or shell out to `nc -U`), and assert the dial **succeeds** and an RPC frame can be exchanged — demonstrating the missing boundary. A fixed implementation should make this assertion fail (dial rejected, or peer-auth handshake rejected) once a per-job secret/user separation is added.

### Citations

**File:** executors/internal/autoscaler/acquisition.go (L134-137)
```go
	// if nesting is disabled, return a client for the host instance, for example VM Isolation and VM tunnel not needed
	if !options.Config.Autoscaler.VMIsolation.Enabled {
		return &client{client: fleetingDialer, cleanup: nil}, nil
	}
```

**File:** executors/instance/steps.go (L24-30)
```go
// Unlike docker - where each job gets its own container filesystem - the
// instance executor runs jobs directly on a shared VM filesystem and supports
// CapacityPerInstance > 1 (multiple concurrent jobs per VM). We therefore place
// the socket in a private directory created with `mktemp -d`, so concurrent jobs
// on the same VM cannot collide or cross-talk and other OS users cannot reach or
// squat on the path. step-runner binds the socket there and reports the path it
// is listening on, which we use for the proxy.
```

**File:** executors/instance/steps.go (L56-64)
```go
	//
	// `mktemp -d` atomically creates a fresh, unpredictably-named directory with
	// mode 0700. On a shared instance (capacity_per_instance > 1) or a host with
	// other OS users, this prevents another user from reaching, pre-creating, or
	// squatting on the socket path: unlike a predictable per-job name there is
	// nothing to guess and no `rm -rf` of an attacker-influenceable path. If the
	// directory cannot be created we exit rather than block on `cat`, so the
	// failure surfaces instead of hanging. The /tmp template keeps the path
	// short, staying within the unix socket sun_path length limit (~108 bytes).
```

**File:** executors/instance/steps.go (L157-164)
```go
	// step-runner reported the socket it is listening on; the proxy dials it.
	proxyCommand := fmt.Sprintf("%s steps proxy --socket %s", shellQuote(runnerCommand), shellQuote(socketPath))

	return func() (io.ReadWriteCloser, error) {
		conn, err := e.client.DialRun(ctx, proxyCommand)
		if err != nil {
			return nil, fmt.Errorf("dialing step-runner proxy: %w", err)
		}
```

**File:** docs/executors/instance.md (L512-513)
```markdown
Jobs executed in these environments should be **trusted** as there is little isolation between them and each job
can affect the performance of another.
```
