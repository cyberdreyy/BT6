### Title
Unsanitized DB error text disclosed to unauthenticated client via `AuthenticateByToken` / `jsonAPIError` - ([File: core/web/auth/auth.go])

### Summary
When `FindUserByAPIToken` returns an error that is neither `sql.ErrNoRows` nor `clsessions.ErrUserSessionExpired`, `AuthenticateByToken` returns the raw error unmodified, and the `Authenticate` middleware forwards it directly to `jsonAPIError`, which serializes `err.Error()` into the JSON body sent to the unauthenticated client. This can leak internal database/driver error text to any client that can trigger a non-`ErrNoRows` failure on the API-token lookup query.

### Finding Description
`AuthenticateByToken` reads `X-API-KEY`/`X-API-SECRET` headers from an unauthenticated request and calls `authr.FindUserByAPIToken(ctx, token.AccessKey)`: [1](#0-0) 

The underlying implementation in `localauth.orm.FindUserByAPIToken` runs a parameterized query and returns whatever `sqlutil.DataSource.GetContext` produces without any wrapping or sanitization: [2](#0-1) 

Back in `AuthenticateByToken`, only `sql.ErrNoRows` and `ErrUserSessionExpired` are mapped to the generic `auth.ErrorAuthFailed`; any other error (e.g., a driver-level error, context cancellation/timeout, connection error, or malformed input causing an encoding error) is returned as-is: [3](#0-2) 

The `Authenticate` middleware loop propagates this error straight into `jsonAPIError`: [4](#0-3) 

`jsonAPIError` only special-cases `*models.JSONAPIErrors`; for any other error type it calls `err.Error()` directly into the JSON response body sent to the client: [5](#0-4) 

Because the query is parameterized (`WHERE token_key = $1`), classic SQL injection is not possible, and the "oversized AccessKey" precondition in the prompt is unlikely to itself produce a distinct DB error for a `text`/`varchar` column without a length constraint. However, the code path is structurally unsound: *any* non-`ErrNoRows` DB-layer failure (transient connection error, statement timeout, context cancellation, driver-level encoding error, etc.) that occurs while processing an unauthenticated request's `X-API-KEY` header will have its raw `Error()` string returned verbatim to the client in the 401 body. This violates the general principle that internal error/stack detail should never reach the client, though it stops short of returning secrets (passwords, tokens, stack traces) since it's a single-line Go error string.

### Impact Explanation
This is a low-severity information-disclosure issue: it can expose internal implementation details (e.g., DB driver error text, occasionally including query fragments or connection info such as `pq: ...` messages) to an unauthenticated attacker, aiding reconnaissance for further attacks. It does not by itself allow authentication bypass, privilege escalation, or credential/secret disclosure — the returned `user` struct is discarded, and no session/token material is included in the error path.

### Likelihood Explanation
Requires the attacker to trigger a genuine backend error on the `FindUserByAPIToken` query path (e.g. transient DB connectivity issues, statement timeouts, or driver-level failures) rather than a normal not-found condition — the specific "oversized string" trigger mentioned in the prompt is not reliably reproducible against a parameterized query on a `text`-typed column. As such, exploitability is opportunistic/timing-dependent rather than deterministically attacker-triggerable from external input alone.

### Recommendation
Wrap/sanitize any error returned by `FindUserByAPIToken` (and similarly `AuthorizedUserWithSession`) before returning it up the call chain in `AuthenticateByToken`/`AuthenticateBySession`, converting all non-`ErrNoRows` failures to a generic internal error (logged server-side, e.g., via `lggr.Errorw`) and returning a generic `auth.ErrorAuthFailed`-style or 500-level opaque error to the client instead of the raw `error.Error()` text.

### Proof of Concept
Go handler-level test plan:
1. Construct a fake `Authenticator` mock whose `FindUserByAPIToken` returns a non-`sql.ErrNoRows` error containing a distinguishable marker string, e.g. `errors.New("pq: internal db detail XYZ")`.
2. Call `auth.Authenticate(mockAuthr, auth.AuthenticateByToken)` as gin middleware against a request with `X-API-KEY`/`X-API-SECRET` headers set.
3. Assert the HTTP response body (401) contains the marker string `"pq: internal db detail XYZ"`, confirming the raw error propagates to the client via `jsonAPIError`.
4. Repeat with `sql.ErrNoRows` and confirm the body instead contains the generic `auth.ErrorAuthFailed` message, demonstrating inconsistent handling for other error types.

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

**File:** core/sessions/localauth/orm.go (L48-53)
```go
// FindUserByAPIToken will attempt to return an API user via the user's table token_key column.
func (o *orm) FindUserByAPIToken(ctx context.Context, apiToken string) (user sessions.User, err error) {
	sql := "SELECT * FROM users WHERE token_key = $1"
	err = o.ds.GetContext(ctx, &user, sql, apiToken)
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
