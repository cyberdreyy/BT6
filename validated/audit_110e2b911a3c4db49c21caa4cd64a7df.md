Confirmed: the Azure resolver's `Resolve()` calls `newVaultService(secret.Server)` → `NewAzureKeyVault`, and `GetSecret` uses `context.Background()` with no timeout, unlike the AWS resolver which wraps its call in `context.WithTimeout(context.Background(), contextTimeout)`.### Title
Azure Key Vault secret resolution uses an unbounded `context.Background()` and runs before any cancellable/timeout context exists, allowing an unresponsive attacker-controlled endpoint to hang the runner indefinitely and unresponsive to job cancellation - ([File: helpers/azure_key_vault/service/azure_key_vault.go])

### Summary
`NewAzureKeyVault` builds an `azsecrets.Client` from the fully job/pipeline-controlled `spec.AzureKeyVaultServer.URL`, `TenantID`, and `ClientID` with no allowlist or validation, and `GetSecret` issues the request with `context.Background()` (no deadline). Worse, `Build.Run` calls `resolveSecrets` (which invokes this resolver) *before* it creates the job's cancellable/timeout context and registers the cancel function with the trace, so a slow/non-responding endpoint can hang the secrets-resolution stage indefinitely, immune to job cancellation.

### Finding Description
- `spec.AzureKeyVaultServer.expandVariables` only expands the `JWT` field via CI variables; `URL`, `TenantID`, and `ClientID` come directly from the `secrets:` block in `.gitlab-ci.yml`, which is pipeline-author-controlled input. [1](#0-0) 
- `NewAzureKeyVault` passes `server.URL` straight into `azsecrets.NewClient(vaultURL, cred, nil)` with no host/IP allowlist or validation. [2](#0-1) 
- `GetSecret` performs the network call with `context.Background()`, which has no deadline and cannot be canceled. [3](#0-2) 
- Critically, in `Build.Run`, `resolveSecrets(trace)` is invoked at line 1549, **before** the cancellable job context is created (`ctx, cancel := context.WithTimeout(ctx, ...)` at line 1560) and before `b.configureTrace(trace, cancel)` registers the cancel function with the trace (line 1563). This means during the "Resolving secrets" stage there is no cancel function registered at all — canceling the job via the GitLab UI/API at this point has no effect, since `trace.SetCancelFunc`/`SetAbortFunc` haven't been wired up yet. [4](#0-3) 
- `attemptResolveSecrets`/`executeResolveSecretsStage` retries the resolution via `retry.NewNoValue(...).Run()` but never passes or derives any context with a deadline into the resolver chain. [5](#0-4) 
- By contrast, the AWS Secrets Manager resolver deliberately wraps its call in `context.WithTimeout(context.Background(), contextTimeout)`, showing that a bounded context is the intended safeguard elsewhere in the same secrets subsystem but is missing for Azure Key Vault. [6](#0-5) 
- The Azure resolver simply calls `newVaultService(secret.Server)` then `s.GetSecret(name, version)` with no timeout wrapper at all. [7](#0-6) 

No allowlist, no per-call deadline, and no linkage to the job's cancellable context exist anywhere on this path, so none of the existing protections (image allowlists, path validation, masking, auth checks) apply here — they are irrelevant to this specific network-call/timeout gap.

### Impact Explanation
A pipeline author who can define `secrets: <name>: azure_key_vault: server: url: <attacker-controlled or internal host>` can cause the runner process to open a TCP connection from the runner host and block indefinitely in the "Resolving secrets" build stage if the target endpoint accepts the connection but never completes the HTTP response (a slow-loris style stall). Because this occurs before the job's `ctx`/cancel function exist, normal job cancellation (UI cancel button, API abort, trace abort) has no effect on this stage — the runner goroutine and its open socket to the attacker-influenced endpoint persist until the process is restarted or an OS-level TCP timeout (which can be very long, often hours) eventually fires. This is a concrete violation of the stated invariant that job input must not cause host-directed persistent connections outliving job cancellation, and it also creates a runner-side resource-exhaustion/DoS surface (blocked worker slot) independent of the eventual job timeout, since the stage runs pre-timeout-context setup.

### Likelihood Explanation
Preconditions are minimal and fully attacker-reachable: any pipeline/job author able to define a `secrets:` entry with `azure_key_vault` in `.gitlab-ci.yml` — a standard, documented CI feature, not an admin-only capability. No special runner configuration is required beyond the secrets feature being enabled. The bug is deterministic and repeatable: pointing the URL at any endpoint that accepts a connection and stalls the HTTP response reliably reproduces the hang, and the ordering bug (resolveSecrets before ctx/cancel setup) is a structural code-path issue independent of network timing.

### Recommendation
1. Wrap the Azure Key Vault (and any other resolver lacking one) `GetSecret` call in a bounded `context.WithTimeout` (as already done for AWS), and thread that context through `NewAzureKeyVault`/`azsecrets.NewClient` calls (via `azcore.ClientOptions` transport timeouts or context-per-call).
2. Reorder `Build.Run` so that the job's cancellable/timeout context is created and registered with the trace (`b.configureTrace`) before `resolveSecrets` is invoked, ensuring job cancellation can interrupt secret resolution just like any other build stage.
3. Consider adding an explicit HTTP client `ResponseHeaderTimeout`/overall request timeout at the `azcore.ClientOptions` level as defense in depth, independent of caller-supplied context.

### Proof of Concept
```go
func TestAzureKeyVault_ResolveSecrets_HangsOnCancel(t *testing.T) {
    // Start an internal HTTP server that accepts the connection but never responds.
    ln, err := net.Listen("tcp", "127.0.0.1:0")
    require.NoError(t, err)
    defer ln.Close()

    connReceived := make(chan net.Conn, 1)
    go func() {
        conn, _ := ln.Accept()
        connReceived <- conn
        // never write a response; keep connection open
    }()

    build := &common.Build{
        Job: common.Job{
            Secrets: spec.Secrets{
                "VAULT": spec.Secret{
                    AzureKeyVault: &spec.AzureKeyVaultSecret{
                        Name: "k", Version: "v",
                        Server: spec.AzureKeyVaultServer{
                            URL: fmt.Sprintf("http://%s", ln.Addr().String()),
                            TenantID: "t", ClientID: "c", JWT: "jwt",
                        },
                    },
                },
            },
        },
    }

    ctx, cancel := context.WithCancel(context.Background())
    done := make(chan error, 1)
    go func() { done <- build.Run(ctx, globalConfig, trace) }()

    conn := <-connReceived
    cancel() // simulate job cancellation / trace abort

    select {
    case <-done:
        // expect Run to return promptly after cancel
    case <-time.After(3 * time.Second):
        t.Fatal("secret resolution did not honor job cancellation")
    }

    // Assert the connection to the internal server was closed within bound.
    conn.SetReadDeadline(time.Now().Add(2 * time.Second))
    buf := make([]byte, 1)
    _, err = conn.Read(buf)
    assert.Error(t, err, "expected connection to internal service to be closed after cancellation")
}
```
Expected (current buggy) behavior: `Run` does not return, and the connection to the internal listener remains open past the assertion window, proving cancellation does not propagate into secret resolution and the request has no deadline.

### Citations

**File:** common/spec/spec.go (L775-777)
```go
func (s *AzureKeyVaultServer) expandVariables(vars Variables) {
	s.JWT = vars.ExpandValue(s.JWT)
}
```

**File:** helpers/azure_key_vault/service/azure_key_vault.go (L23-50)
```go
func NewAzureKeyVault(server spec.AzureKeyVaultServer) (AzureKeyVault, error) {
	v := new(defaultAzureKeyVault)

	getAssertion := func(c context.Context) (string, error) {
		return server.JWT, nil
	}

	cred, err := azidentity.NewClientAssertionCredential(
		server.TenantID,
		server.ClientID,
		getAssertion,
		&azidentity.ClientAssertionCredentialOptions{
			ClientOptions: azcore.ClientOptions{},
		})

	if err != nil {
		return nil, fmt.Errorf("getting credential failed: %w", err)
	}

	vaultURL := server.URL
	client, err := azsecrets.NewClient(vaultURL, cred, nil)
	if err != nil {
		return nil, fmt.Errorf("initializing azure key Vault service: %w", err)
	}

	v.client = client
	return v, err
}
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

**File:** common/build.go (L1542-1563)
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

	b.configureTrace(trace, cancel)
```

**File:** common/build.go (L1742-1786)
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

func (b *Build) executeResolveSecretsStage(trace JobTrace) error {
	b.OnBuildStageStartFn.Call(BuildStageResolveSecrets)
	defer b.OnBuildStageEndFn.Call(BuildStageResolveSecrets)

	section := helpers.BuildSection{
		Name:        string(BuildStageResolveSecrets),
		SkipMetrics: !b.Job.Features.TraceSections,
		Run: func() error {
			logger := b.getNewLogger(trace, b.Log(), false)
			defer logger.Close()

			resolver, err := b.secretsResolver(&logger, GetSecretResolverRegistry(), b.IsFeatureFlagOn)
			if err != nil {
				return fmt.Errorf("creating secrets resolver: %w", err)
			}

			variables, err := resolver.Resolve(b.Secrets)
			if err != nil {
				return fmt.Errorf("resolving secrets: %w", err)
			}

			b.secretsVariables = variables
			b.RefreshAllVariables()

			return nil
		},
	}

	return section.Execute(&b.logger)
}
```

**File:** helpers/secrets/resolvers/aws/aws_secrets_manager_resolver.go (L103-105)
```go
	ctx, cancel := context.WithTimeout(context.Background(), contextTimeout)
	defer cancel()

```

**File:** helpers/secrets/resolvers/azure_key_vault/azure_key_vault_resolver.go (L36-57)
```go
func (v *azureKeyVaultResolver) Resolve() (string, error) {
	if !v.IsSupported() {
		return "", secrets.NewResolvingUnsupportedSecretError(resolverName)
	}

	secret := v.secret.AzureKeyVault
	s, err := newVaultService(secret.Server)
	if err != nil {
		return "", err
	}

	name := secret.Name
	version := secret.Version

	data, err := s.GetSecret(name, version)

	if err != nil {
		return "", err
	}

	return fmt.Sprintf("%v", data), nil
}
```
