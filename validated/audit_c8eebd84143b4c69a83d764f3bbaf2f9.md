### Title
Raw database error messages leaked to unauthenticated clients via `errors.Wrap(err, "finding external initiator")` - ([File: core/web/auth/auth.go])

### Summary
`AuthenticateExternalInitiator` and `AuthenticateByToken` in `core/web/auth/auth.go` return the raw, wrapped DB error for any failure other than `sql.ErrNoRows`, and this error propagates unmodified to `jsonAPIError`, which serializes `err.Error()` directly into the HTTP response body sent to the unauthenticated caller.

### Finding Description
In `AuthenticateExternalInitiator` (`core/web/auth/auth.go:119-133`), when `store.FindExternalInitiator(ctx, eia)` returns an error that is not `sql.ErrNoRows`, the code executes `return errors.Wrap(err, "finding external initiator")` [1](#0-0) . The same pattern exists in `AuthenticateByToken` for `FindUserByAPIToken` errors that are not `sql.ErrNoRows`/`ErrUserSessionExpired` [2](#0-1) .

This error is returned up through the `Authenticate` gin middleware, which calls `jsonAPIError(c, http.StatusUnauthorized, err)` [3](#0-2) . `jsonAPIError` checks only whether the error is a `*models.JSONAPIErrors`; if not, it falls back to `c.JSON(statusCode, models.NewJSONAPIErrorsWith(err.Error()))`, embedding the raw `err.Error()` string — including the underlying DB driver error text — directly into the JSON response body returned to the client [4](#0-3) .

Since a plain `errors.Wrap` result is a generic `*errors.withMessage`/`*errors.withStack` type, not `*models.JSONAPIErrors`, it always falls into the `err.Error()` branch, meaning any non-`ErrNoRows` DB-layer error (e.g., connection failure, timeout, driver-level error, malformed query parameter causing a driver error) is disclosed verbatim to an unauthenticated caller.

### Impact Explanation
This is an information-disclosure issue: an unauthenticated caller could see internal Postgres/driver error text (e.g., connection string fragments, table/column identifiers, or query parameter echoes) rather than a generic "unauthorized" message. It does not directly yield credentials, session tokens, or job/fund control, and does not bypass authentication or authorization — `auth.ErrorAuthFailed` still gates success. The impact is limited to internal detail exposure that could aid reconnaissance for further attacks, corresponding to a low-severity "information disclosure" class rather than a critical/high-severity node compromise.

### Likelihood Explanation
Triggering a *non*-`ErrNoRows` DB error from unauthenticated attacker-controlled HTTP headers is not trivial: `FindExternalInitiator`/`FindUserByAPIToken` queries use parameterized statements, so ordinary malformed/oversized header values are unlikely to induce anything but a normal "no rows" outcome or a successful/failed auth comparison — not a distinct SQL error. Reliably forcing this path (e.g., via DB connection exhaustion, timeout, or a driver-level failure) generally requires conditions outside attacker control from a pure HTTP-header manipulation angle. This reduces real-world exploitability significantly even though the code path for leaking the error text is real and reachable in principle.

### Recommendation
In `AuthenticateExternalInitiator` and `AuthenticateByToken`, avoid propagating raw wrapped DB errors to the HTTP layer. Log the detailed error server-side (e.g., via `logger`), and return a generic `auth.ErrorAuthFailed` (or another sanitized sentinel error) to the caller instead of `errors.Wrap(err, "finding external initiator")`. Additionally, harden `jsonAPIError` so that any error which isn't a recognized `models.JSONAPIErrors` type is rendered with a generic message rather than `err.Error()`, unless explicitly allow-listed as safe.

### Proof of Concept
1. In a Go test using `httptest`, construct a mock `Authenticator`/ORM whose `FindExternalInitiator` returns a non-`sql.ErrNoRows` error, e.g. `errors.New("pq: connection reset by peer, dsn=postgres://user:pass@host/db")` to simulate a driver-level failure.
2. Wrap this mock in the `auth.Authenticate` gin middleware with `AuthenticateExternalInitiator` as the only auth method, and issue an unauthenticated request with `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers set to arbitrary values.
3. Assert the response status is `401` and inspect the JSON body: confirm it contains `"finding external initiator: pq: connection reset by peer, dsn=postgres://user:pass@host/db"` (i.e., the raw wrapped error text), demonstrating that `jsonAPIError` leaks the underlying error string via `models.NewJSONAPIErrorsWith(err.Error())`.
4. Repeat for `AuthenticateByToken` with a mocked `FindUserByAPIToken` returning a non-`sql.ErrNoRows` error to confirm the same leak pattern.

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

**File:** core/web/auth/auth.go (L126-133)
```go
	ei, err := store.FindExternalInitiator(ctx, eia)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return auth.ErrorAuthFailed
		}

		return errors.Wrap(err, "finding external initiator")
	}
```

**File:** core/web/auth/auth.go (L166-171)
```go
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
