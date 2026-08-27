### Title
Raw DB error propagated to unauthenticated client via jsonAPIError - ([File: core/web/auth/auth.go])

### Summary
`AuthenticateByToken` only converts `sql.ErrNoRows` and `clsessions.ErrUserSessionExpired` from `FindUserByAPIToken` into the generic `auth.ErrorAuthFailed`; any other error (e.g. a wrapped DB timeout/connection error from `orm.FindUserByAPIToken`, which runs `SELECT * FROM users WHERE token_key = $1`) is returned unmodified. The `Authenticate` middleware then passes this raw error to `jsonAPIError`, which serializes `err.Error()` directly into the JSON response body sent to the unauthenticated caller.

### Finding Description
`core/web/auth/auth.go` `AuthenticateByToken` (lines 92-99):
```go
user, err := authr.FindUserByAPIToken(ctx, token.AccessKey)
if err != nil {
    if errors.Is(err, sql.ErrNoRows) || errors.Is(err, clsessions.ErrUserSessionExpired) {
        return auth.ErrorAuthFailed
    }
    return err
}
``` [1](#0-0) 

The concrete implementation `core/sessions/localauth/orm.go` `FindUserByAPIToken` runs a raw SQL query and returns whatever error the driver/ORM produces unwrapped:
```go
func (o *orm) FindUserByAPIToken(ctx context.Context, apiToken string) (user sessions.User, err error) {
	sql := "SELECT * FROM users WHERE token_key = $1"
	err = o.ds.GetContext(ctx, &user, sql, apiToken)
	return
}
``` [2](#0-1) 

Any error other than `sql.ErrNoRows` or `sessions.ErrUserSessionExpired` (e.g., a connection timeout, context-cancellation error, or scanning error containing column/table names) is returned as-is by `AuthenticateByToken` to the calling `Authenticate` middleware:
```go
if err != nil {
    c.Abort()
    jsonAPIError(c, http.StatusUnauthorized, err)
    return
}
``` [3](#0-2) 

`jsonAPIError` in `core/web/auth/helpers.go` then serializes `err.Error()` verbatim into the JSON response body:
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
``` [4](#0-3) 

Since `err` is not a `*models.JSONAPIErrors`, the `else` branch fires and `err.Error()` (the raw underlying error string, potentially including driver-level details) is placed in the `detail` field of the JSON response returned with HTTP 401 to the unauthenticated caller. This is reachable by any unauthenticated client simply by sending a request with `X-API-KEY`/`X-API-SECRET` headers to any token-authenticated route registered in `core/web/router.go`.

However, the actual sensitivity of the leaked information depends entirely on what `sqlx`/`lib/pq`/`database/sql` errors look like in practice. Standard Go SQL driver errors (e.g., `sql: connection is already closed`, `pq: ...` errors like syntax errors, connection refused, or context deadline exceeded) typically do not include full SQL statements or full schema unless the query itself is malformed by attacker input — which is not the case here since the query is a fixed, parameterized string (`token.AccessKey` is passed as a bound parameter, not concatenated). So realistically the exposed detail is limited to generic driver/connection-level messages (e.g., "driver: bad connection", "pq: too many connections", "context deadline exceeded"), not SQL fragments or arbitrary schema data, since this is a parameterized query with no attacker-controlled SQL construction.

### Impact Explanation
This is an information disclosure of internal error text (potentially including database driver name, connection state, or transient internal state) to an unauthenticated caller. It does not, by itself, provide credential exposure, authentication bypass, or privilege escalation — the request still fails with 401 regardless of what error message is returned, and no secrets (tokens, hashes, salts) are included in the returned `sessions.User` object since it's never touched by `jsonAPIError`. This falls into a low-severity information disclosure class (internal error/driver detail leakage), not a critical secret-confinement violation, because the query is parameterized and cannot be manipulated to leak arbitrary table contents or SQL structure via the `AccessKey` header value.

### Likelihood Explanation
Triggering this path requires an actual backend/DB level failure (e.g., DB connection drop, timeout, or driver-level fault) coinciding with an incoming token-authenticated request — this is not something an unauthenticated attacker can reliably or deterministically trigger through request content alone, since the query is parameterized and the `AccessKey` header cannot inject a custom ORM error path. It would occur opportunistically during outages/DB stress, not via a crafted "AccessKey" value as the question's proof idea suggests.

### Recommendation
In `core/web/auth/auth.go` `AuthenticateByToken`, treat all other errors from `FindUserByAPIToken` as internal server errors rather than passing them straight through to the HTTP response, e.g., log the error and return a generic wrapped error, and update `Authenticate`/`jsonAPIError` (or add a dedicated error type) to return a generic "internal error" detail string for non-`JSONAPIErrors` errors instead of `err.Error()`.

### Proof of Concept
Go handler-level test plan:
1. Implement a mock `Authenticator` (using `core/sessions/mocks` pattern or an inline stub) whose `FindUserByAPIToken` returns a custom error, e.g. `errors.New("pq: dial tcp 10.0.0.5:5432: connect: connection refused")`.
2. Set up a gin router with `auth.Authenticate(mockAuthr, auth.AuthenticateByToken)` middleware as in `core/web/auth/auth_test.go`.
3. Send a request with `X-API-KEY`/`X-API-SECRET` headers set.
4. Assert the HTTP status is 401 and inspect the JSON body's `errors[0].detail` field.
5. Assert whether `detail` equals the raw mock error string (confirming leakage) versus a generic message — currently it will equal the raw string, confirming the passthrough behavior described above, though with limited real-world sensitivity given the parameterized query.

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

**File:** core/web/auth/auth.go (L166-171)
```go
		if err != nil {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, err)

			return
		}
```

**File:** core/sessions/localauth/orm.go (L49-53)
```go
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
