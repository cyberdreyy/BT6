### Title
Vault secret resolution runs outside any job context/deadline, allowing an unprivileged job to hang the resolve stage on an attacker-controlled Vault endpoint - (File: helpers/secrets/resolvers/vault/resolver.go)

### Summary
`resolver.Resolve` calls `newVaultService(url, namespace, secret)` (`service.NewVault` → `defaultVault.initialize` → `prepareAuthenticatedClient` → `vault.NewClient`/`client.Authenticate`) without ever passing a `context.Context` or deadline. `Build.Run` calls `b.resolveSecrets(trace)` *before* it creates the per-build timeout context (`ctx, cancel := context.WithTimeout(ctx, b.GetBuildTimeout())`), so the entire secret-resolution stage, including the Vault HTTP calls made deep inside the `openbao/openbao/api` client, is not bound by the job's build timeout at all.

### Finding Description
The call chain is exactly as described:
`resolver.Resolve` (`helpers/secrets/resolvers/vault/resolver.go:38-63`) → `newVaultService` (`service.NewVault`, `helpers/vault/service/vault.go:42-51`) → `defaultVault.initialize` → `prepareAuthenticatedClient` (`helpers/vault/service/vault.go:62-81`) → `newVaultClient` (`vault.NewClient`, `helpers/vault/client.go:65-83`) and `client.Authenticate` (`helpers/vault/client.go:85-94`), which in turn calls the pluggable `auth.Authenticate(c)` (e.g. JWT auth method) that performs an HTTP round-trip to the attacker-supplied `vault.Server.URL`.

None of these functions accept or propagate a `context.Context`. Compare this to the sibling AWS Secrets Manager resolver, which explicitly does `ctx, cancel := context.WithTimeout(context.Background(), contextTimeout)` before making external calls (`helpers/secrets/resolvers/aws/aws_secrets_manager_resolver.go:103-108`) — the Vault resolver has no equivalent safeguard.

Worse, in `common/build.go`, `Build.Run` calls `b.resolveSecrets(trace)` at line 1549, and only afterward constructs the build-wide cancellable context at line 1560 (`ctx, cancel := context.WithTimeout(ctx, b.GetBuildTimeout())`). This means secret resolution executes entirely outside of any job-scoped context or deadline — even the eventual per-job timeout cannot interrupt it, because it hasn't been created yet.

An unprivileged pipeline author fully controls `spec.Secret.Vault.Server.URL`/`Namespace` via CI/CD variables mapped into the job's secret spec (this is a documented, user-facing GitLab CI feature — `secrets: VAULT_TOKEN: vault: engine: ...`). By pointing `Server.URL` at a host that accepts a TCP connection but never responds (or responds with a slow-drip HTTP body), `client.Authenticate` will block on the underlying Go `net/http` client used by `github.com/openbao/openbao/api/v2`, which — unless a client/response timeout is explicitly configured on the `http.Config`/`api.Config` — has no default deadline for the whole request lifetime.

`b.attemptResolveSecrets` additionally wraps this in a retry loop (`GetSecretsRetrievalAttempts`, default configurable 1–10), amplifying the hang if it does eventually error, but the primary issue is the indefinite block on a single attempt.

### Impact Explanation
The resolve stage runs synchronously in the job's execution goroutine, inside `helpers.BuildSection.Execute`, before the executor is prepared. A goroutine blocked indefinitely on `client.Authenticate` ties up that job's worker thread/goroutine for as long as the hung connection is held (as no read/dial timeout applies), and — because it precedes context.WithTimeout construction — it is not bounded by `GetBuildTimeout()`. In runner configurations with a bounded `concurrent`/executor pool (e.g., shared Docker/shell/custom executors with limited concurrency), a job stuck in this state occupies a slot indefinitely, and since it is not reachable via the job cancellation context, GitLab-side job cancellation or trace abort (`configureTrace`/`SetCancelFunc`) also cannot interrupt it because that cancel func is wired to the context created *after* this call. This can starve concurrency slots that would otherwise serve other projects' jobs, matching the "persistent multi-tenant disruption" scope.

### Likelihood Explanation
Feasible and fully attacker-reachable: any pipeline author who can set `secrets:<name>:vault:server: {url, namespace}` (standard secrets-management CI syntax) controls the exact inputs used in `resolver.Resolve`. No special runner privileges are needed — this is a job-level configuration surface, and no existing check validates or bounds Vault server reachability/time. The bug is deterministic and repeatable: any TCP endpoint that never completes the handshake response reproduces it every run.

### Recommendation
- Thread a `context.Context` (derived from the build/job context, with an explicit secret-resolution timeout similar to the AWS resolver's `contextTimeout` pattern) through `SecretResolver.Resolve`, `newVaultService`, `defaultVault.initialize/prepareAuthenticatedClient`, and `vault.Client.Authenticate`, ultimately setting it on the underlying `api.Client`'s HTTP request context.
- Alternatively/additionally, configure the underlying `openbao/api` client with an explicit `http.Client{Timeout: ...}` so all requests (dial, TLS handshake, response) are bounded regardless of caller context propagation.
- Move `b.resolveSecrets(trace)` in `common/build.go` to occur after `ctx, cancel := context.WithTimeout(ctx, b.GetBuildTimeout())` is established, or otherwise ensure secret resolution is itself wrapped in its own bounded context independent of build timeout ordering, so job cancellation can always interrupt it.

### Proof of Concept
```go
// helpers/vault/service/vault_test.go (new test, integration-style)
func TestPrepareAuthenticatedClient_HangsOnSlowServer(t *testing.T) {
    // Start a TCP listener that accepts connections but never writes a response.
    ln, _ := net.Listen("tcp", "127.0.0.1:0")
    defer ln.Close()
    go func() {
        for {
            conn, err := ln.Accept()
            if err != nil {
                return
            }
            // Never respond; hold the connection open indefinitely.
            _ = conn
        }
    }()

    done := make(chan error, 1)
    go func() {
        _, err := NewVault("http://"+ln.Addr().String(), "ns", someAuthWithJWTFactory)
        done <- err
    }()

    select {
    case <-done:
        // If it returns, verify duration was bounded (should have failed fast due to context)
    case <-time.After(5 * time.Second):
        t.Fatal("NewVault/prepareAuthenticatedClient blocked with no timeout/context enforcement")
    }
}
```
Expected (current, vulnerable) result: the test times out after 5s, demonstrating the call has no bounded deadline. After the fix (context propagation + client timeout), the call should return an error within the configured timeout window, and the goroutine/connection should be observably torn down (e.g., via `runtime.NumGoroutine()` delta or listener accept-loop instrumentation). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** helpers/secrets/resolvers/vault/resolver.go (L38-63)
```go
func (v *resolver) Resolve() (string, error) {
	if !v.IsSupported() {
		return "", secrets.NewResolvingUnsupportedSecretError(resolverName)
	}

	secret := v.secret.Vault

	url := secret.Server.URL
	namespace := secret.Server.Namespace

	s, err := newVaultService(url, namespace, secret)
	if err != nil {
		return "", classifyError(err)
	}

	data, err := s.GetField(secret, secret)
	if err != nil {
		return "", classifyError(err)
	}

	if data == nil {
		return "", common.ErrSecretNotFound
	}

	return fmt.Sprintf("%v", data), nil
}
```

**File:** helpers/vault/service/vault.go (L42-81)
```go
func NewVault(url string, namespace string, auth Auth) (Vault, error) {
	v := new(defaultVault)

	err := v.initialize(url, namespace, auth)
	if err != nil {
		return nil, fmt.Errorf("initializing Vault service: %w", err)
	}

	return v, nil
}

func (v *defaultVault) initialize(url string, namespace string, auth Auth) error {
	err := v.prepareAuthenticatedClient(url, namespace, auth)
	if err != nil {
		return fmt.Errorf("preparing authenticated client: %w", err)
	}

	return nil
}

func (v *defaultVault) prepareAuthenticatedClient(url string, namespace string, authDetails Auth) error {
	client, err := newVaultClient(url, namespace)
	if err != nil {
		return err
	}

	auth, err := v.prepareAuthMethodAdapter(authDetails)
	if err != nil {
		return err
	}

	err = client.Authenticate(auth)
	if err != nil {
		return err
	}

	v.client = client

	return nil
}
```

**File:** helpers/vault/client.go (L65-94)
```go
func NewClient(apiURL string, namespace string, opts ...ClientOption) (Client, error) {
	client, err := api.NewClient(&api.Config{Address: apiURL})
	if err != nil {
		return nil, fmt.Errorf("creating new Vault client: %w", unwrapAPIResponseError(err))
	}

	client.SetNamespace(namespace)

	for _, opt := range opts {
		client, err = opt(client)
		if err != nil {
			return nil, err
		}
	}

	return &defaultClient{
		internal: client,
	}, nil
}

func (c *defaultClient) Authenticate(auth AuthMethod) error {
	err := auth.Authenticate(c)
	if err != nil {
		return fmt.Errorf("authenticating Vault client: %w", err)
	}

	c.internal.SetToken(auth.Token())

	return nil
}
```

**File:** common/build.go (L1542-1561)
```go
	err = b.expandInputs()
	if err != nil {
		return &BuildError{FailureReason: ConfigurationError, Inner: err}
	}

	b.printRunningWithHeader(trace)

	err = b.resolveSecrets(trace)
	if err != nil {
		return wrapSecretResolvingError(err)
	}

	b.expandContainerOptions()
	b.logUsedImages()

	b.logger = b.getNewLogger(trace, b.Log(), false)
	defer b.logger.Close()

	ctx, cancel := context.WithTimeout(ctx, b.GetBuildTimeout())
	defer cancel()
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

**File:** helpers/secrets/resolvers/aws/aws_secrets_manager_resolver.go (L103-108)
```go
	ctx, cancel := context.WithTimeout(context.Background(), contextTimeout)
	defer cancel()

	secret := v.secret.AWSSecretsManager

	s, err := newAWSSecretsManagerService(ctx, region, identity)
```
