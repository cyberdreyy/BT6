### Title
Raw database error text is disclosed to unauthenticated API-token requests via `AuthenticateByToken` - ([File: core/web/auth/auth.go])

### Summary
`AuthenticateByToken` only maps `sql.ErrNoRows` and `clsessions.ErrUserSessionExpired` to the generic `auth.ErrorAuthFailed`; any other error from `FindUserByAPIToken` (e.g. a DB error caused by a malformed `X-API-KEY` value) is returned verbatim and rendered to the client by `jsonAPIError`, which serializes `err.Error()` directly into the JSON response body. This lets an unauthenticated caller with no valid credentials observe raw SQL/driver error text.

### Finding Description
`AuthenticateByToken` in `core/web/auth/auth.go` calls `authr.FindUserByAPIToken(ctx, token.AccessKey)` [1](#0-0) . The concrete implementation, `orm.FindUserByAPIToken` in `core/sessions/localauth/orm.go`, runs `SELECT * FROM users WHERE token_key = $1` with the attacker-supplied `AccessKey` and returns whatever error the driver produces [2](#0-1) .

Back in `AuthenticateByToken`, only `sql.ErrNoRows` and `clsessions.ErrUserSessionExpired` are normalized to `auth.ErrorAuthFailed`; any other error (e.g. a Postgres constraint/type/encoding error triggered by a crafted `AccessKey`) is returned as-is on line 98 [3](#0-2) .

This error propagates up through the `Authenticate` middleware, which — regardless of the specific error — calls `jsonAPIError(c, http.StatusUnauthorized, err)` for any non-nil error [4](#0-3) . `jsonAPIError` checks whether the error is a `*models.JSONAPIErrors`; a raw driver/SQL error is not, so it falls through to `c.JSON(statusCode, models.NewJSONAPIErrorsWith(err.Error()))`, embedding the literal `err.Error()` string (which can contain driver-specific text, e.g. Postgres error codes, malformed input details, or fragments of the query) into the HTTP response body sent to the attacker [5](#0-4) .

No authentication, role check, or presenter redaction sits between this and the caller — `AuthenticateByToken` is invoked before any user object exists, so this is reachable by a fully unauthenticated request that merely sets an `X-API-KEY`/`X-API-SECRET` header pair.

### Impact Explanation
This is an information-disclosure bug: a completely unauthenticated caller can trigger a DB-layer error and receive the raw error text in the 401 response body. This can leak internal implementation details (driver name/version hints, column types, encoding constraints) which could assist in enumeration or further probing, but it does not by itself grant authentication bypass, session hijacking, or any state-changing action — it stays within the "error message reveals internal details" class of information disclosure rather than a direct credential/secret leak, since `FindUserByAPIToken`'s query does not return password/secret material to the caller and `AuthenticateUserByToken`'s constant-time comparison is never reached in this path.

### Likelihood Explanation
Feasibility is moderate: triggering a *query-level* error (not simply "no matching row," which is the common case) via a single string parameter bound with `$1` requires an input that causes the driver/database itself to error out (e.g., invalid byte sequence for the configured encoding, or a value exceeding a column type constraint) rather than a normal SQL injection (parameterized queries are used, so classic injection is not possible). This is plausible for certain encodings/column types but is not guaranteed on every deployment/database configuration, and requires no credentials at all — just crafting a header value, which is straightforward to attempt repeatedly.

### Recommendation
In `core/web/auth/auth.go`, do not propagate raw datastore errors to the HTTP layer from `AuthenticateByToken`. Log the underlying error server-side and return `auth.ErrorAuthFailed` (or another generic error) for any error returned by `FindUserByAPIToken`, mirroring the pattern already used for `sql.ErrNoRows`/`ErrUserSessionExpired`. More broadly, `jsonAPIError` should avoid embedding raw `err.Error()` text for errors that are not already sanitized/user-facing `JSONAPIErrors`.

### Proof of Concept
Go handler-level integration test:
1. Build a `gin.Engine` wired with `auth.Authenticate(store, auth.AuthenticateByToken)` protecting a dummy handler, using a mock `Authenticator`.
2. Configure the mock `FindUserByAPIToken` to return a non-`sql.ErrNoRows`, non-`ErrUserSessionExpired` error, e.g. `errors.New("pq: invalid byte sequence for encoding \"UTF8\": 0x00")`, simulating a DB-level failure from a crafted `AccessKey`.
3. Send a request with `X-API-KEY: <crafted-value>` and `X-API-SECRET: anything`.
4. Assert the response status is 401 and assert the JSON body's error message does **not** equal/contain the mock's raw error string — the fix should make it return a generic message equivalent to `auth.ErrorAuthFailed.Error()` instead. Before the fix, this assertion fails because the raw DB error text appears verbatim in the response body.

### Citations

**File:** core/web/auth/auth.go (L92-99)
```go
	// We need to first load the user row so we can compare tokens using the stored salt
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
