### Title
Raw DB error from AuthenticateByToken leaks internal error details to unauthenticated attacker - ([File: core/web/auth/auth.go])

### Summary
`AuthenticateByToken` only converts `sql.ErrNoRows` and `clsessions.ErrUserSessionExpired` into the generic `auth.ErrorAuthFailed`; any other error from `authr.FindUserByAPIToken` (e.g. a raw DB/connection error) is returned unmodified. `Authenticate` then passes that raw error straight into `jsonAPIError`, which serializes `err.Error()` verbatim into the HTTP response body sent to the caller.

### Finding Description
In `AuthenticateByToken`:
```go
user, err := authr.FindUserByAPIToken(ctx, token.AccessKey)
if err != nil {
    if errors.Is(err, sql.ErrNoRows) || errors.Is(err, clsessions.ErrUserSessionExpired) {
        return auth.ErrorAuthFailed
    }
    return err
}
``` [1](#0-0) 

Only the two known "not found"/"expired" cases are normalized to the generic auth-failure sentinel. Any other error type (e.g. a Postgres connection failure, a scan error, a context-cancellation error) is returned as-is up through `Authenticate`:
```go
err = method(c, store)
if !errors.Is(err, auth.ErrorAuthFailed) {
    break
}
...
if err != nil {
    c.Abort()
    jsonAPIError(c, http.StatusUnauthorized, err)
    return
}
``` [2](#0-1) 

`jsonAPIError` then places `err.Error()` directly into the JSON body returned to the HTTP client:
```go
func jsonAPIError(c *gin.Context, statusCode int, err error) {
	_ = c.Error(err).SetType(gin.ErrorTypePublic)
	var jsonErr *models.JSONAPIErrors
	if errors.As(err, &jsonErr) {
		c.JSON(statusCode, jsonErr)
		return
	}
	c.JSON(statusCode, models.NewJSONAPIErrorsWith(err.Error()))
}
``` [3](#0-2) 

Since `err` is not a `*models.JSONAPIErrors`, it falls to `models.NewJSONAPIErrorsWith(err.Error())`, which embeds the raw error text in the HTTP response body returned with a `401 Unauthorized` to any unauthenticated caller supplying `X-API-KEY`/`X-API-SECRET` headers.

The attacker only needs to send arbitrary header values to any route wrapped with `Authenticate(authr, AuthenticateByToken)`; no valid credentials are required — the vulnerable path is reached on the DB error itself, not after successful auth.

### Impact Explanation
An unauthenticated attacker who triggers a transient/atypical DB error path (e.g., during DB failover, connection pool exhaustion, or a driver-level error unrelated to "no rows") could receive raw internal error text (potentially containing SQL fragments, column/table names, or other backend implementation details) in the API response. This matches Chainlink's "information disclosure of internal implementation/config detail" class rather than a critical secret leak (no credentials, private keys, or session tokens are exposed by this path specifically) — but it does violate the general principle of not returning raw internal errors to unauthenticated clients.

### Likelihood Explanation
Exploitability depends entirely on `FindUserByAPIToken` actually returning a non-`sql.ErrNoRows`/non-`ErrUserSessionExpired` error, which normally only happens under DB-level failure conditions (not attacker-controlled on a healthy DB). An attacker cannot deterministically trigger this on demand without also being able to cause the underlying store to fail (e.g., cause connection exhaustion), which is not confirmed to be attacker-triggerable through this code path alone. Under normal operation the underlying `localauth` ORM's `FindUserByAPIToken` implementation would return `sql.ErrNoRows` for any nonexistent token, and other errors would be rare/transient rather than reliably reproducible by an unprivileged client.

### Recommendation
In `AuthenticateByToken`, avoid propagating raw underlying errors to `Authenticate`/`jsonAPIError`. Wrap the unexpected error in a generic message before returning (e.g., `return errors.New("internal error")` after logging the real error via `logger`), consistent with how `AuthenticateExternalInitiator` wraps external-initiator lookup errors, and ensure `jsonAPIError` never emits a `err.Error()` string for non-JSONAPI errors on auth failure paths.

### Proof of Concept
Add a Go unit test in `core/web/auth/auth_test.go`:
1. Create a stub/mock `Authenticator` whose `FindUserByAPIToken` returns a custom error, e.g. `errors.New("dial tcp 10.0.0.5:5432: connect: connection refused (user=postgres_admin_secret)")`.
2. Build a `gin.Context` with `X-API-KEY`/`X-API-SECRET` headers set, wrap the mock with `Authenticate(mockAuthr, AuthenticateByToken)`, and invoke the handler.
3. Assert the HTTP status is 401 and inspect the response body via `httptest.ResponseRecorder`.
4. Assert failure of the test currently: the body contains the literal string `"connection refused (user=postgres_admin_secret)"`, demonstrating the raw error leaks into the response — expected/fixed behavior would have the body contain only a generic message (e.g. `"Unauthorized"`).

### Citations

**File:** core/web/auth/auth.go (L93-99)
```go
	user, err := authr.FindUserByAPIToken(ctx, token.AccessKey)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) || errors.Is(err, clsessions.ErrUserSessionExpired) {
			return auth.ErrorAuthFailed
		}
		return err
	}
```

**File:** core/web/auth/auth.go (L160-171)
```go
		for _, method := range methods {
			err = method(c, store)
			if !errors.Is(err, auth.ErrorAuthFailed) {
				break
			}
		}
		if err != nil {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, err)

			return
		}
```

**File:** core/web/auth/helpers.go (L15-23)
```go
func jsonAPIError(c *gin.Context, statusCode int, err error) {
	_ = c.Error(err).SetType(gin.ErrorTypePublic)
	var jsonErr *models.JSONAPIErrors
	if errors.As(err, &jsonErr) {
		c.JSON(statusCode, jsonErr)
		return
	}
	c.JSON(statusCode, models.NewJSONAPIErrorsWith(err.Error()))
}
```
