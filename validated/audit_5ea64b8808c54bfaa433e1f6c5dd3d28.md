### Title
Internal error messages (including raw DB error text) can leak to unauthenticated clients via `jsonAPIError` in the auth pipeline - ([File: core/web/auth/helpers.go])

### Summary
`jsonAPIError` unconditionally serializes `err.Error()` into the JSON:API response body whenever the error is not already a `*models.JSONAPIErrors`. `AuthenticateByToken` and `CreateSession`'s caller (`SessionsController.Create`) both forward raw, unwrapped errors from datastore calls straight into `jsonAPIError`, meaning any error other than the expected "no such user"/"auth failed" sentinel is returned to the requester verbatim.

### Finding Description
`jsonAPIError` has no allowlist or redaction logic — it either passes through an existing `*models.JSONAPIErrors`, or falls back to `models.NewJSONAPIErrorsWith(err.Error())`, embedding the raw Go error string in the HTTP response: [1](#0-0) 

In `AuthenticateByToken`, only `sql.ErrNoRows` and `clsessions.ErrUserSessionExpired` are normalized to the generic `auth.ErrorAuthFailed`; any other error from `FindUserByAPIToken` (e.g., a database connectivity error, query failure, or driver-level error) is returned unmodified: [2](#0-1) 

That error then propagates through the `Authenticate` middleware, which — since it is not `auth.ErrorAuthFailed` — is passed directly into `jsonAPIError` with `http.StatusUnauthorized`: [3](#0-2) 

Similarly, `SessionsController.Create` forwards the raw error from `CreateSession` (an unauthenticated, attacker-reachable endpoint taking `email`/`password` in the request body) directly to `jsonAPIError`: [4](#0-3) 

An unauthenticated attacker can send arbitrary `X-API-KEY`/`X-API-SECRET` headers or POST arbitrary JSON to `/sessions`, and if the backing datastore call fails for any reason other than the two normalized cases (e.g., a transient DB outage, connection pool exhaustion, or any unexpected error surfaced by the ORM), the resulting response body contains the raw `err.Error()` string instead of a generic message.

### Impact Explanation
This is an information-disclosure issue: depending on the underlying driver/ORM behavior, error strings can include internal details such as query fragments, table/column names, or infrastructure-related error text. It does not by itself grant authentication bypass or privilege escalation, but it can aid an attacker in reconnaissance of the node's internal database implementation. This is a low-severity confinement/hardening issue rather than direct fund-movement or auth-bypass impact, since triggering it requires the datastore itself to be in an erroring state (not something the pure application-layer attacker without DB/network conditions can force reliably).

### Likelihood Explanation
Reaching the vulnerable code path requires the underlying database call to fail in a way not covered by the `sql.ErrNoRows` / `ErrUserSessionExpired` checks — this typically requires host/infra-level conditions (DB downtime, connection issues) rather than something a remote, unprivileged attacker can deterministically induce purely via crafted API request payloads. Without confirmed evidence that ordinary malformed/attacker-controlled input (e.g., a crafted API key string) can deterministically force a non-`ErrNoRows` DB error through normal parameterized queries, exploitability by an unprivileged network attacker alone is uncertain/low.

### Recommendation
Wrap or normalize all datastore errors returned from `FindUserByAPIToken`, `AuthorizedUserWithSession`, and `CreateSession` to a generic sentinel (e.g., `auth.ErrorAuthFailed` or a generic `errors.New("internal server error")`) before they reach `jsonAPIError`, and log the original error server-side only. Consider making `jsonAPIError` itself strip non-allowlisted error content for 401/500 status codes by default, requiring callers to opt in to expose specific safe messages.

### Proof of Concept
1. Unit test constructing a mock `Authenticator` whose `FindUserByAPIToken` returns a custom error (e.g., `errors.New("pq: connection to server at \"internal-db-host\" failed")`) instead of `sql.ErrNoRows`.
2. Call `AuthenticateByToken` (or drive the full `Authenticate` middleware) with valid-looking `X-API-KEY`/`X-API-SECRET` headers.
3. Assert the resulting HTTP response body (via `jsonAPIError`) contains the raw injected string, confirming the internal error text is directly exposed instead of a generic "unauthorized" message.
4. Repeat similarly for `SessionsController.Create` by mocking `AuthenticationProvider().CreateSession` to return a synthetic DB error and asserting the `/sessions` POST response body leaks it.

### Citations

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

**File:** core/web/auth/auth.go (L92-104)
```go
	// We need to first load the user row so we can compare tokens using the stored salt
	user, err := authr.FindUserByAPIToken(ctx, token.AccessKey)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) || errors.Is(err, clsessions.ErrUserSessionExpired) {
			return auth.ErrorAuthFailed
		}
		return err
	}

	ok, err := clsessions.AuthenticateUserByToken(token, &user)
	if err != nil {
		return err
	}
```

**File:** core/web/auth/auth.go (L159-174)
```go
		var err error
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

		c.Next()
	}
```

**File:** core/web/sessions_controller.go (L56-60)
```go
	sid, err := sc.App.AuthenticationProvider().CreateSession(ctx, sr)
	if err != nil {
		jsonAPIError(c, http.StatusUnauthorized, err)
		return
	}
```
