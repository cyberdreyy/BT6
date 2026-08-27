### Title
Authentication failure paths leak raw internal error strings (DB/internal errors) to unauthenticated clients via jsonAPIError - ([File: core/web/auth/helpers.go], [File: core/web/auth/auth.go])

### Summary
`AuthenticateBySession` and `AuthenticateByToken` in `core/web/auth/auth.go` only convert specific, expected errors (e.g. `sql.ErrNoRows`, `clsessions.ErrUserSessionExpired`) into the generic `auth.ErrorAuthFailed`. Any other error returned by the underlying store methods (`AuthorizedUserWithSession`, `FindUserByAPIToken`, `clsessions.AuthenticateUserByToken`) is returned unmodified and eventually reaches `jsonAPIError`, which calls `models.NewJSONAPIErrorsWith(err.Error())` and serializes the raw error text into the HTTP response body sent to the unauthenticated caller.

### Finding Description
The `Authenticate` middleware [1](#0-0)  loops over auth methods and, on any non-`ErrorAuthFailed` error, calls `jsonAPIError(c, http.StatusUnauthorized, err)` directly with the raw error object returned by the method.

In `AuthenticateBySession`, if `AuthorizedUserWithSession` returns any error other than being caught upstream, it is returned as-is with no wrapping/sanitization: [2](#0-1) .

In `AuthenticateByToken`, only `sql.ErrNoRows` and `clsessions.ErrUserSessionExpired` are normalized to `auth.ErrorAuthFailed`; any other error from `FindUserByAPIToken` (e.g., a DB connectivity/driver error) is returned unmodified, and any error from `clsessions.AuthenticateUserByToken` (e.g., bcrypt/crypto errors) is also returned unmodified: [3](#0-2) .

`jsonAPIError` then does not attempt to redact or generalize plain (non-`*models.JSONAPIErrors`) errors — it directly serializes `err.Error()` into the JSON response: [4](#0-3) .

This means any underlying error type that is not explicitly special-cased (e.g. database driver errors, timeout errors, encoding errors, or any other unexpected internal error surfaced by the auth provider implementation) would have its `.Error()` string returned verbatim in the 401 response body to an unauthenticated caller. This differs from the generic `"Unauthorized"` / `"not a valid session"` messages used in the role-check helpers (`RequiresRunRole`, `RequiresEditRole`, `RequiresAdminRole`), which explicitly construct generic error messages rather than forwarding internal errors.

### Impact Explanation
If the underlying authentication provider (session or token store) ever returns a non-`sql.ErrNoRows` error containing internal details (e.g., driver-specific SQL error text, connection strings, or other backend state), that text is disclosed directly to an unauthenticated client. This matches Chainlink's "Information disclosure" bounty class — it can aid enumeration/fingerprinting of internal node state (DB backend behavior, timeouts, internal error codes) without requiring any credentials, though it does not by itself grant authentication bypass or fund movement.

### Likelihood Explanation
No preconditions are required — the attacker just needs to be able to send unauthenticated HTTP requests to any endpoint protected by `AuthenticateBySession`/`AuthenticateByToken`. The likelihood of actually triggering a *sensitive* internal error depends on the specific error paths in the concrete `Authenticator` implementations (`core/sessions/localauth`, `core/sessions/ldapauth`, `core/sessions/oidcauth`) — I was not able to fully enumerate every error type each implementation can return within the available context, so the extent of genuinely sensitive information exposed (vs. benign generic Go error text) is uncertain and would need further investigation of those provider implementations.

### Recommendation
In `AuthenticateBySession` and `AuthenticateByToken`, treat *all* non-nil errors from the authenticator as `auth.ErrorAuthFailed` for the purposes of the client-facing response (log the original error server-side via `c.Error(err)` for diagnostics, but do not forward `err.Error()` to `jsonAPIError`). Alternatively, harden `jsonAPIError` itself to only forward `models.JSONAPIErrors` errors verbatim, and replace any other error type with a fixed generic message (e.g., `"Unauthorized"`) before calling `models.NewJSONAPIErrorsWith`.

### Proof of Concept
Go unit test plan:
1. In `core/web/auth/auth_test.go`, create a mock `Authenticator` (via `core/sessions/mocks/authentication_provider.go`) whose `AuthorizedUserWithSession` returns a distinctive non-`sql.ErrNoRows` error, e.g. `errors.New("dial tcp 10.0.0.5:5432: connect: connection refused")`.
2. Call `Authenticate(mockAuthr, AuthenticateBySession)` as a gin handler against a test request with a session cookie set, and capture the HTTP response body.
3. Assert that the JSON error body does NOT contain the substring `"10.0.0.5"` or `"connect: connection refused"` — currently this assertion would fail because `jsonAPIError` forwards `err.Error()` verbatim.
4. Repeat for `AuthenticateByToken` with `FindUserByAPIToken` returning a similar non-sentinel error, and separately with `clsessions.AuthenticateUserByToken` returning an internal crypto error.
5. Expected (fixed) behavior: response body only ever contains a generic message (e.g., `"Unauthorized"`), regardless of the underlying error's content.

### Citations

**File:** core/web/auth/auth.go (L63-66)
```go
	user, err := authr.AuthorizedUserWithSession(ctx, sessionID)
	if err != nil {
		return err
	}
```

**File:** core/web/auth/auth.go (L93-107)
```go
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
	if !ok {
		return auth.ErrorAuthFailed
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
