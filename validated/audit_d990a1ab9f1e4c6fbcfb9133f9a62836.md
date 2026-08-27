### Title
Raw internal errors from `FindUserByAPIToken` are serialized verbatim into unauthenticated HTTP error responses - (File: `core/web/auth/auth.go`)

### Summary
`AuthenticateByToken` only converts `sql.ErrNoRows` and `clsessions.ErrUserSessionExpired` into the generic `auth.ErrorAuthFailed`; any other error from `authr.FindUserByAPIToken` (e.g. a DB timeout, closed connection, or driver-level error) is returned unchanged. This raw error is passed straight into `jsonAPIError`, which calls `err.Error()` and puts the resulting string into the JSON response body sent to the unauthenticated caller.

Note: the function actually lives in `core/web/auth/auth.go`, not `core/web/middleware.go` — the latter file only contains static-asset/gzip serving code and has no `Authenticate`/`AuthenticateByToken` logic. The underlying issue described in the question does exist, just in the correctly-named file.

### Finding Description
`AuthenticateByToken` reads the `X-API-KEY`/`X-API-SECRET` headers from an arbitrary, unauthenticated request and calls: [1](#0-0) 

Only two specific error types are normalized to the safe sentinel `auth.ErrorAuthFailed`; every other error path (e.g. `context.Canceled`/`context.DeadlineExceeded` from a client disconnecting mid-query, a driver error, or a DB constraint failure) falls through to `return err`, propagating the raw error.

This propagates into `Authenticate`, which stops iterating through the auth methods as soon as it sees an error that is not `auth.ErrorAuthFailed`, and passes it directly to `jsonAPIError`: [2](#0-1) 

`jsonAPIError` then serializes `err.Error()` verbatim into the JSON body when the error is not already a `*models.JSONAPIErrors`: [3](#0-2) 

Because `AuthenticateByToken` is reachable by any unauthenticated caller simply by supplying the `X-API-KEY`/`X-API-SECRET` headers (no valid credentials required to reach the DB lookup), an attacker can trigger any failure mode of the underlying `FindUserByAPIToken` query (e.g., by aborting the TCP connection mid-request so the query's context is canceled, or during a DB outage/connection-pool exhaustion) and receive that raw error text back in the HTTP response body.

### Impact Explanation
This is an information-disclosure issue: raw DB/driver error strings (which can include SQL fragments, table/column names, connection details, or internal Go error wrapping) are returned to an unauthenticated network caller instead of a generic "unauthorized" message. This aids reconnaissance for further attacks (e.g., SQL injection fingerprinting, infra footprinting) but by itself does not grant authentication bypass, privilege escalation, or fund movement.

### Likelihood Explanation
- Requires no credentials — the attacker only needs to send `X-API-KEY`/`X-API-SECRET` headers to reach `FindUserByAPIToken`.
- Requires an underlying DB error to occur, which is not fully attacker-controlled; the most feasible attacker-triggerable variant is aborting the connection to induce a context-cancellation error from the DB driver, which typically yields a low-sensitivity message ("context canceled") rather than schema details. Triggering a genuinely sensitive DB error (e.g. a real driver/schema error) generally requires an actual outage/misconfiguration on the backend, which is outside attacker control.
- Overall likelihood is low-to-moderate and impact is limited to leaking generic driver text unless a real backend fault coincides with the request.

### Recommendation
In `AuthenticateByToken` (and similarly `AuthenticateBySession`/`AuthenticateExternalInitiator`), do not propagate the raw datastore error to the HTTP layer. Wrap/replace any error not already recognized as `auth.ErrorAuthFailed` with a generic error before returning, e.g.:
```go
user, err := authr.FindUserByAPIToken(ctx, token.AccessKey)
if err != nil {
    if errors.Is(err, sql.ErrNoRows) || errors.Is(err, clsessions.ErrUserSessionExpired) {
        return auth.ErrorAuthFailed
    }
    // log the real error internally, return generic error to caller
    return auth.ErrorAuthFailed
}
```
Ensure `jsonAPIError` is only ever passed sentinel/generic errors for the unauthenticated auth path, and log the original error server-side for debugging.

### Proof of Concept
1. Unit test in `core/web/auth/auth_test.go`: create a mock `Authenticator` whose `FindUserByAPIToken` returns a non-`sql.ErrNoRows` error, e.g. `errors.New("pq: relation \"users\" does not exist")` or `context.Canceled`.
2. Call `AuthenticateByToken(ctx, mockAuthr)` directly and assert the returned error equals the raw mock error (demonstrating it is not normalized).
3. Handler-level integration test: wrap `Authenticate(mockAuthr, AuthenticateByToken)` as gin middleware, send a request with `X-API-KEY`/`X-API-SECRET` headers, configure the mock to return the raw DB-style error, and assert:
   - HTTP status `401`
   - Response JSON body contains the literal mock error string (e.g., `"pq: relation ..."`) instead of a generic message — proving the leak.
4. Recommended fix validated by re-running the same test after normalizing the error and asserting the body now contains only a generic "unauthorized" message.

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

**File:** core/web/auth/auth.go (L157-175)
```go
func Authenticate(store Authenticator, methods ...authMethod) gin.HandlerFunc {
	return func(c *gin.Context) {
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
