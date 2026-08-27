Based on the code path traced: `AuthenticateByToken` in `core/web/auth/auth.go` explicitly returns raw, unwrapped errors from `authr.FindUserByAPIToken(ctx, token.AccessKey)` and from `clsessions.AuthenticateUserByToken(token, &user)` whenever those errors are not `sql.ErrNoRows` / `clsessions.ErrUserSessionExpired`, and this propagates unmodified through `Authenticate` (`core/web/auth/auth.go` lines 78-114, 157-175) directly into `jsonAPIError`, which serializes `err.Error()` verbatim into the JSON body. [1](#0-0) [2](#0-1) [3](#0-2) 

However, I was unable to fully verify the actual body of `FindUserByAPIToken` in `core/sessions/localauth/orm.go` — the index only surfaced matches by filename/count, not the function contents, and I ran out of tool iterations before reading it. Whether this database query implementation ever wraps SQL driver errors with query fragments or bound parameter values (e.g., via `sq.Sqlx`/named exec debug info, or a wrapped `pq.Error`) is the key remaining fact needed to confirm actual secret/query leakage versus a generic driver error message like "connection refused" or "context deadline exceeded." Standard Go `database/sql`/sqlx usage typically does not embed query text or the token value itself into returned errors unless explicit wrapping does so (e.g. `errors.Wrapf(err, "querying for token %s", token)`), and I could not confirm or rule out such wrapping in this repo.

Given this, I can confirm the **architectural flaw** (unsanitized error passthrough to an unauthenticated caller) is real and reachable via `POST/GET /v2/*` routes protected by `auth.Authenticate(..., auth.AuthenticateByToken, auth.AuthenticateBySession)` (see `core/web/router.go` lines 245-248, 450-454), triggered by supplying a non-empty `X-API-KEY`/`X-API-SECRET` pair designed to produce a DB error other than "no rows" (e.g., a value causing a query timeout, encoding error, or driver-level failure). But I cannot state with certainty that the underlying error strings actually contain "SQL fragments, session IDs, or partial tokens" without inspecting `FindUserByAPIToken`'s implementation and any error-wrapping it does — that would require another read of `core/sessions/localauth/orm.go`.

### Title
Unauthenticated DB/backend errors leaked verbatim in JSON response via `jsonAPIError` - (File: core/web/auth/helpers.go)

### Summary
`jsonAPIError` serializes `err.Error()` directly into the HTTP JSON response for any error that isn't already a `*models.JSONAPIErrors`, and `AuthenticateByToken`/`AuthenticateBySession` pass through raw backend errors (anything other than `sql.ErrNoRows`/`ErrorAuthFailed`/`ErrUserSessionExpired`) unmodified to this function, reaching an unauthenticated caller who supplied a malformed but non-empty token/secret.

### Finding Description
`AuthenticateByToken` calls `authr.FindUserByAPIToken(ctx, token.AccessKey)`; if the returned error is not `sql.ErrNoRows` or `ErrUserSessionExpired`, it does `return err` unmodified rather than mapping to `auth.ErrorAuthFailed`. `Authenticate` then calls `jsonAPIError(c, http.StatusUnauthorized, err)`, which — because the error is not a `*models.JSONAPIErrors` — falls through to `c.JSON(statusCode, models.NewJSONAPIErrorsWith(err.Error()))`, placing the raw `err.Error()` string in the response body sent to the requester. This is reachable pre-authentication on every `/v2/*` route gated by `auth.AuthenticateByToken` (e.g. `/v2/ping`, `/v2/users`) since the attacker only needs to supply non-empty `X-API-KEY`/`X-API-SECRET` headers that cause the backing store to fail with something other than "not found." Whether the leaked string actually contains sensitive material (query text, DSN fragments, bound values) depends on the concrete error types produced by the session store's `FindUserByAPIToken`/`AuthenticateUserByToken`, which I could not fully verify in this pass.

### Impact Explanation
If the underlying error strings do carry internal details (e.g., driver-specific messages, wrapped context including the attacker-supplied token, or infra details), this is an information-disclosure issue aiding reconnaissance (matches Chainlink's "sensitive data exposure" bounty class), but does not by itself grant authentication bypass or fund movement — impact is bounded to information leakage.

### Likelihood Explanation
Low-to-moderate feasibility: the attacker needs a fully unauthenticated network path (satisfied, since these routes only require guessed headers) but also needs a way to reliably force a *non*-`sql.ErrNoRows` failure from the auth store (e.g., DB timeout, oversized/invalid encoding causing a driver error) — this is harder to trigger deterministically than a simple wrong-credential 401, lowering practical likelihood without further confirmation of what errors `FindUserByAPIToken`/`AuthenticateUserByToken` can realistically produce.

### Recommendation
In `AuthenticateByToken`/`AuthenticateBySession`, wrap/replace any non-"not found" backend error before returning it (e.g., log the raw error server-side, return a generic `auth.ErrorAuthFailed` or a fixed opaque error to the caller) instead of propagating `err` as-is; alternatively, harden `jsonAPIError` to never emit raw internal `error.Error()` strings for 401/500-class errors originating from authentication paths, always substituting a static message like "unauthorized" and logging details only server-side via `c.Error(err)`.

### Proof of Concept
Write a Go unit test in `core/web/auth/auth_test.go` using a mock `Authenticator` (via `core/sessions/mocks/authentication_provider.go`) whose `FindUserByAPIToken` returns a sentinel error such as `errors.New("pq: syntax error near SELECT users WHERE token='abc123secret'")`. Set up a `gin` test context with `httptest.NewRecorder()`, call `auth.Authenticate(mockAuthr, auth.AuthenticateByToken)` with valid non-empty `X-API-KEY`/`X-API-SECRET` headers, then assert: (1) HTTP status is 401, (2) the JSON response body's `errors[].detail` field equals or contains the exact sentinel string ("abc123secret"/"pq: syntax error"), confirming the internal error text is echoed back verbatim to the (still unauthenticated) caller.

### Citations

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
