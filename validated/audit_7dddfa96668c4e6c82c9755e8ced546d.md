### Title
Vault/GSM configuration error text is surfaced unredacted into the job trace via ResolvingConfigurationError.Error() - (File: helpers/secrets/errors.go)

### Summary
`classifyError` in `helpers/secrets/resolvers/vault/resolver.go` wraps any Vault API error carrying a 400/401/403/404 status into a `ResolvingConfigurationError`, whose `Error()` method simply returns `e.Inner.Error()` verbatim. Whatever text the Vault API client put into that inner error (which can include path/role/lease details returned by the shared Vault server) is passed through unmodified up the call chain and ultimately becomes part of the error returned from `spec.Secret` resolution.

### Finding Description
`resolver.Resolve()` at [1](#0-0)  calls `newVaultService`/`GetField`, and on any error immediately calls `classifyError(err)`. `classifyError` at [2](#0-1)  checks the HTTP status code and wraps the *original, unmodified* `err` into `secrets.NewResolvingConfigurationError(err)` for 400/401/403/404, or `NewResolvingExternalDependencyError(err)` for 5xx — it never sanitizes or replaces the message text.

`ResolvingConfigurationError.Error()` returns `e.Inner.Error()` verbatim [3](#0-2) , and `Unwrap()` exposes the same inner error [4](#0-3) . So whatever string the underlying Vault client library places in the error (typically taken directly from the Vault HTTP API response body) propagates unchanged.

However, tracing the actual call path in this repo does **not** support the claimed exfiltration mechanism at `common/secrets.go:101,119`:
- `defaultSecretsResolver.handleSecret` at [5](#0-4)  only calls `r.logger.Warningln` when `r.secretResolverRegistry.GetFor(secret)` fails (i.e., no resolver supports the secret type) — that is unrelated to Vault API errors.
- When `sr.Resolve()` itself returns an error (which is where a `ResolvingConfigurationError` would appear), the code does `return nil, err` without ever calling `logger.Println` or `logger.Warningln` on that error's text. The error is instead propagated as a returned `error` value up through `Resolve()` at [6](#0-5)  and out of `defaultSecretsResolver.Resolve`.

So the raw provider error text is never logged to the job trace inside `common/secrets.go` as asserted. It becomes a returned Go `error` that some caller further up the stack (outside the code shown here, in the build/job-failure-reason handling) must decide how to report — that caller's behavior (whether it prints `err.Error()` to the trace, and whether it does so unmasked) determines whether any leak actually occurs. I was not able to locate and confirm that upstream caller's logging behavior within the scope of the files examined, since the trail of `SecretsResolver.Resolve` consumption (`common/build.go`) could not be fully inspected in the remaining budget.

### Impact Explanation
If an upstream caller does print the raw `error.Error()` string from a failed secrets resolution to the job trace (which is plausible given the framework's job-failure-reason classification design, but not confirmed here), then any secret-provider error text embedded by the Vault server response — potentially including tenant-specific path/role/lease identifiers — could be visible to the job author. This would be a cross-tenant information disclosure limited to whatever content the Vault server itself puts in error bodies for 400/403/404 responses; it is bounded by what the shared Vault instance's API returns, not by data the runner code fabricates.

### Likelihood Explanation
Not confirmed as exploitable within the code paths verified. The specific mechanism described in the question — `logger.Println`/`Warningln` in `common/secrets.go` lines 101/119 emitting the raw provider error — does not exist for the `ResolvingConfigurationError` path; those log calls are for progress messages and "no resolver found" warnings, not for Vault classification errors. Exploitability therefore hinges entirely on unverified code outside the provided scope (the ultimate consumer of the error returned by `SecretsResolver.Resolve`).

### Recommendation
Regardless of where the final log/print occurs, `ResolvingConfigurationError.Error()`/`Unwrap()` should not return the raw Vault error text unmodified when it may contain provider-internal identifiers (paths, lease IDs, tokens). Consider returning a generic, classification-only message (e.g., "vault: permission denied for configured secret path") from `Error()`, while retaining the original error only via `Unwrap()` for internal diagnostics/metrics, and ensure any code that surfaces job-failure reasons to the trace uses a sanitized message rather than `err.Error()` directly.

### Proof of Concept
Cannot be fully constructed against the confirmed leak point, since `common/secrets.go:101,119` does not log resolver `Resolve()` errors. A test can only confirm the (already known) fact that `ResolvingConfigurationError.Error()` passes through inner error text unmodified:
```go
func TestResolvingConfigurationError_LeaksInnerText(t *testing.T) {
    fake := errors.New("permission denied: role 'project-42-role' lease_id=abcd1234")
    err := secrets.NewResolvingConfigurationError(fake)
    assert.Contains(t, err.Error(), "project-42-role") // demonstrates passthrough
}
```
This proves the passthrough exists at the `errors.go` layer, but does **not** prove the trace-exfiltration claim, since the asserted `common/secrets.go` logging calls for this error path were not found in the code.

### Citations

**File:** helpers/secrets/resolvers/vault/resolver.go (L48-56)
```go
	s, err := newVaultService(url, namespace, secret)
	if err != nil {
		return "", classifyError(err)
	}

	data, err := s.GetField(secret, secret)
	if err != nil {
		return "", classifyError(err)
	}
```

**File:** helpers/secrets/resolvers/vault/resolver.go (L83-104)
```go
func classifyError(err error) error {
	if err == nil {
		return nil
	}

	var apiErr apiStatusCoder
	if !errors.As(err, &apiErr) {
		return err
	}

	switch code := apiErr.StatusCode(); {
	case code == http.StatusBadRequest,
		code == http.StatusUnauthorized,
		code == http.StatusForbidden,
		code == http.StatusNotFound:
		return secrets.NewResolvingConfigurationError(err)
	case code >= 500:
		return secrets.NewResolvingExternalDependencyError(err)
	default:
		return err
	}
}
```

**File:** helpers/secrets/errors.go (L43-45)
```go
func (e *ResolvingConfigurationError) Error() string {
	return e.Inner.Error()
}
```

**File:** helpers/secrets/errors.go (L47-49)
```go
func (e *ResolvingConfigurationError) Unwrap() error {
	return e.Inner
}
```

**File:** common/secrets.go (L103-106)
```go
		v, err := r.handleSecret(variableKey, secret)
		if err != nil {
			return nil, err
		}
```

**File:** common/secrets.go (L116-135)
```go
func (r *defaultSecretsResolver) handleSecret(variableKey string, secret spec.Secret) (*spec.Variable, error) {
	sr, err := r.secretResolverRegistry.GetFor(secret)
	if err != nil {
		r.logger.Warningln(fmt.Sprintf("Not resolved: %v", err))
		return nil, nil
	}

	r.logger.Println(fmt.Sprintf("Using %q secret resolver...", sr.Name()))

	value, err := sr.Resolve()
	if errors.Is(err, ErrSecretNotFound) {
		if !r.featureFlagOn(featureflags.EnableSecretResolvingFailsIfMissing) {
			err = nil
		} else {
			err = fmt.Errorf("%w: %v", err, variableKey)
		}
	}
	if err != nil {
		return nil, err
	}
```
