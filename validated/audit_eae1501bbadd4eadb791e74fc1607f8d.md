### Title
`GetSecret` hardcodes `context.Background()`, so job cancellation cannot interrupt an in-flight Azure Key Vault call, leaking a blocked goroutine/socket per cancelled job - ([File: helpers/azure_key_vault/service/azure_key_vault.go])

### Summary
`defaultAzureKeyVault.GetSecret` calls `v.client.GetSecret(context.Background(), ...)` instead of accepting/propagating a caller context, and the retry loop wrapping this call (`Build.attemptResolveSecrets` → `retry.retryRun`) also has no context wired to it. Because `resolveSecrets` runs in `Build.Run` *before* the job's cancellable context and trace cancel/abort functions are even created, a build's secret-resolution stage cannot be interrupted by GitLab-side cancel/abort while it is blocked waiting on a non-responsive Key Vault endpoint.

### Finding Description
`Build.Run` calls `b.resolveSecrets(trace)` at [1](#0-0)  — this happens *before* `ctx, cancel := context.WithTimeout(ctx, b.GetBuildTimeout())` and `b.configureTrace(trace, cancel)` are established at [2](#0-1) . `trace.SetCancelFunc`/`SetAbortFunc` (used by `Cancel()`/`Abort()` in `network/trace.go`) are no-ops until a func is registered [3](#0-2) [4](#0-3) , so at the time secrets are being resolved there is no cancel/abort hook registered at all.

Independent of that ordering issue, the retry wrapper used for secret resolution, `retry.New()`, defaults its internal context to `context.Background()` unless `.WithContext()` is explicitly called [5](#0-4) , and `attemptResolveSecrets` never calls `.WithContext(...)` [6](#0-5) . So even the retry-level `ctx.Done()` checks in `retryRun` (lines 190-210) can never fire for a cancelled/aborted job.

Finally, even if a real, cancellable context were threaded all the way down, `defaultAzureKeyVault.GetSecret` still ignores it and always issues the Azure SDK call with `context.Background()`: [7](#0-6) 

This is a structural, three-layer failure to propagate cancellation: (1) resolveSecrets runs before cancel infrastructure exists, (2) the retry helper isn't given a context, and (3) `GetSecret` hardcodes `context.Background()` regardless. An attacker-controlled CI config that declares `secrets: {FOO: {azure_key_vault: {server: {url: "http://<attacker-controlled-non-responsive-host>", ...}}}}` (job variables/CI YAML fields fully under pipeline-author control) causes the runner to open an HTTP connection to that host as part of every job run. If the host accepts the TCP connection but never writes a response, the underlying `net/http` round-trip has no deadline (no context deadline, no `http.Client.Timeout` set in `azsecrets.NewClient(vaultURL, cred, nil)` at [8](#0-7) ), so the call blocks until the OS-level TCP timeout (which can be very long or effectively unbounded absent keepalive probes hitting a firewall drop).

Because `resolveSecrets` is a synchronous, blocking step inside `Build.Run` (called via `section.Execute` and the retry loop), the entire job-processing goroutine for that job is pinned to this blocked HTTP call. GitLab-side cancel/abort has nothing to attach to at this point in the code path, so it cannot unblock it, and even if it could, `GetSecret`'s hardcoded `context.Background()` would ignore the cancellation anyway.

### Impact Explanation
Each job configured with a secret pointing to a non-responsive Azure Key Vault-like endpoint that is cancelled/aborted during secret resolution leaves behind a goroutine and an open socket/FD that cannot be reclaimed until the underlying TCP connection eventually times out at the OS level (which may be minutes to effectively indefinite depending on network path). Because the runner process handles many jobs concurrently across projects, an unprivileged pipeline author repeatedly triggering and cancelling such jobs can accumulate blocked goroutines and open sockets, consuming runner job-concurrency slots and file descriptors. This degrades or exhausts a shared runner's capacity, affecting unrelated projects/jobs scheduled on the same runner manager — matching the described scoped impact.

### Likelihood Explanation
Feasibility is high and fully within an unprivileged pipeline author's control: `azure_key_vault` secret definitions (including `server.url`) are ordinary CI config/job-variable fields expanded via `spec.AzureKeyVaultSecret.expandVariables` [9](#0-8) , requiring no special runner privileges — only the ability to define a job with a secrets block and to cancel that job (both standard GitLab user capabilities). The bug is deterministic given a non-responsive endpoint and repeatable across many sequential job executions, since nothing in the resolver, retry helper, or SDK client layer sets a bounded timeout or wires cancellation through.

### Recommendation
- Change `AzureKeyVault.GetSecret(name, version string)` to accept a `context.Context` parameter and thread it through from `azureKeyVaultResolver.Resolve()`/`SecretsResolver.Resolve()`/`Build.resolveSecrets()`.
- Move `resolveSecrets` (or at least give it) a bounded, cancellable context derived from the job context before/while secret resolution runs, rather than only wiring `trace.SetCancelFunc`/`SetAbortFunc` after this stage completes.
- Call `retry.New().WithContext(ctx)` in `attemptResolveSecrets` so the retry loop can abort on cancellation between attempts.
- In `NewAzureKeyVault`, set a bounded per-request timeout policy on `azcore.ClientOptions` (e.g., `Retry.TryTimeout`) as defense-in-depth against non-responsive endpoints even without cancellation.

### Proof of Concept
Go test plan (integration-style):
```go
func TestAzureKeyVaultGetSecret_RespectsContextCancellation(t *testing.T) {
    // Start an HTTP listener that accepts connections but never writes a response.
    ln, _ := net.Listen("tcp", "127.0.0.1:0")
    go func() {
        for {
            c, err := ln.Accept()
            if err != nil { return }
            // read request, never respond, keep conn open
            go io.Copy(io.Discard, c)
        }
    }()
    defer ln.Close()

    server := spec.AzureKeyVaultServer{
        TenantID: "t", ClientID: "c", JWT: "jwt",
        URL: "http://" + ln.Addr().String(),
    }

    v, err := service.NewAzureKeyVault(server)
    require.NoError(t, err)

    ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
    defer cancel()

    done := make(chan struct{})
    goroutinesBefore := runtime.NumGoroutine()

    go func() {
        _, _ = v.GetSecret("name", "version") // currently ignores ctx entirely
        close(done)
    }()

    select {
    case <-done:
        // Expected once fixed: GetSecret should return promptly once ctx is cancelled/times out.
    case <-time.After(2 * time.Second):
        t.Fatal("GetSecret did not respect cancellation/timeout; call is still blocked")
    }

    // Additionally assert goroutine count returns to baseline shortly after cancellation,
    // which currently fails because GetSecret() uses context.Background() internally.
    assert.Eventually(t, func() bool {
        return runtime.NumGoroutine() <= goroutinesBefore+1
    }, 3*time.Second, 100*time.Millisecond)
}
```
Expected result today: the test fails/times out because `GetSecret` never observes the deadline — it uses `context.Background()` regardless of what the caller intends, demonstrating the leaked, uninterruptible goroutine/socket.

### Citations

**File:** common/build.go (L1547-1552)
```go
	b.printRunningWithHeader(trace)

	err = b.resolveSecrets(trace)
	if err != nil {
		return wrapSecretResolvingError(err)
	}
```

**File:** common/build.go (L1560-1563)
```go
	ctx, cancel := context.WithTimeout(ctx, b.GetBuildTimeout())
	defer cancel()

	b.configureTrace(trace, cancel)
```

**File:** common/build.go (L1742-1755)
```go
func (b *Build) attemptResolveSecrets(trace JobTrace, attempts int) error {
	retryRunner := retry.New().WithMaxTries(attempts)

	if b.IsFeatureFlagOn(featureflags.UseExponentialBackoffStageRetry) {
		backoffConfig := b.getStageRetryBackoffConfig()
		retryRunner = retryRunner.
			WithBackoff(backoffConfig.Min, backoffConfig.Max).
			WithBuildLog(&b.logger)
	}

	return retry.NewNoValue(retryRunner, func() error {
		return b.executeResolveSecretsStage(trace)
	}).Run()
}
```

**File:** network/trace.go (L112-125)
```go
// Cancel consumes the function set by SetCancelFunc.
func (c *clientJobTrace) Cancel() bool {
	c.lock.RLock()
	cancelFunc := c.cancelFunc
	c.lock.RUnlock()

	if cancelFunc == nil {
		return false
	}

	c.SetCancelFunc(nil)
	cancelFunc()
	return true
}
```

**File:** network/trace.go (L140-154)
```go
func (c *clientJobTrace) Abort() bool {
	c.lock.RLock()
	abortFunc := c.abortFunc
	c.lock.RUnlock()

	if abortFunc == nil {
		return false
	}

	c.SetCancelFunc(nil)
	c.SetAbortFunc(nil)

	abortFunc()
	return true
}
```

**File:** helpers/retry/retry.go (L86-97)
```go
func New() *Retry {
	return &Retry{
		check: func(_ int, _ error) bool {
			return true
		},
		backoff: &backoff.Backoff{
			Min: defaultRetryMinBackoff,
			Max: defaultRetryMaxBackoff,
		},
		ctx: context.Background(),
	}
}
```

**File:** helpers/azure_key_vault/service/azure_key_vault.go (L42-49)
```go
	vaultURL := server.URL
	client, err := azsecrets.NewClient(vaultURL, cred, nil)
	if err != nil {
		return nil, fmt.Errorf("initializing azure key Vault service: %w", err)
	}

	v.client = client
	return v, err
```

**File:** helpers/azure_key_vault/service/azure_key_vault.go (L52-63)
```go
func (v *defaultAzureKeyVault) GetSecret(name string, version string) (interface{}, error) {
	resp, err := v.client.GetSecret(context.Background(), name, version, nil)
	if err != nil {
		return nil, fmt.Errorf("getting secret failed: %w", err)
	}

	if resp.Value == nil {
		return "", common.ErrSecretNotFound
	}

	return *resp.Value, err
}
```

**File:** common/spec/spec.go (L768-777)
```go
func (s *AzureKeyVaultSecret) expandVariables(vars Variables) {
	s.Server.expandVariables(vars)

	s.Name = vars.ExpandValue(s.Name)
	s.Version = vars.ExpandValue(s.Version)
}

func (s *AzureKeyVaultServer) expandVariables(vars Variables) {
	s.JWT = vars.ExpandValue(s.JWT)
}
```
