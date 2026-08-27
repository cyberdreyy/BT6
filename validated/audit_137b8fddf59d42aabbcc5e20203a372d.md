### Title
Raw DB/internal error message disclosure to unauthenticated caller via `AuthenticateByToken` non-`ErrNoRows` error path - ([File: core/web/auth/helpers.go])

### Summary
`AuthenticateByToken` in `core/web/auth/auth.go` only normalizes `FindUserByAPIToken` errors to `auth.ErrorAuthFailed` when the error is `sql.ErrNoRows` or `clsessions.ErrUserSessionExpired`; any other error (e.g. a raw driver/DB error) is returned unwrapped. `Authenticate` then passes that raw error straight to `jsonAPIError`, which serializes `err.Error()` verbatim into the JSON response body for an unauthenticated caller.

### Finding Description
In `core/web/auth/auth.go`: [1](#0-0) 

only `sql.ErrNoRows` and `ErrUserSessionExpired` are normalized to the generic `auth.ErrorAuthFailed`; any other error from `authr.FindUserByAPIToken(ctx, token.AccessKey)` is returned as-is (`return err`).

The `Authenticate` middleware wraps all configured `authMethod`s: [2](#0-1) 

Because the loop only continues to the next method (and hides the error) when the error `errors.Is(err, auth.ErrorAuthFailed)`, any non-wrapped raw error breaks out immediately and is passed to `jsonAPIError(c, http.StatusUnauthorized, err)`.

`jsonAPIError` then serializes it directly: [3](#0-2) 
`models.NewJSONAPIErrorsWith(err.Error())` puts the raw Go/driver error string into the HTTP response body sent back to the (unauthenticated) client.

The underlying `FindUserByAPIToken` implementations execute parameterized SQL against the `users` (local auth), `ldap_user_api_tokens`, or `oidc_user_api_tokens` tables, e.g.: [4](#0-3) 

Because the query is parameterized, classic SQL injection is not possible, but a sufficiently malformed/oversized `X-API-KEY` value (or a database connectivity hiccup, statement timeout, or column length constraint violation) can produce a raw driver-level error other than `sql.ErrNoRows` — for example a Postgres `value too long for type character varying(N)` error if the token column is constrained, or a `pq:` driver error carrying internal error codes/messages. Any such error bypasses the `errors.Is(err, sql.ErrNoRows)` check and is propagated unmodified back through `AuthenticateByToken` → `Authenticate` → `jsonAPIError`, exposing raw internal error text (potentially including SQL fragments, driver internals, or table/column names) in the HTTP response to an unauthenticated caller.

Note: the OIDC (`core/sessions/oidcauth/oidc.go`) and LDAP (`core/sessions/ldapauth/ldap.go`) `FindUserByAPIToken` implementations return raw driver errors from `GetContext`/`sqlutil.TransactDataSource` in the same unwrapped fashion, feeding the same code path.

### Impact Explanation
This is an internal error / information disclosure issue: an unauthenticated attacker could receive raw database/driver error strings in the HTTP response, which may reveal table/column names, data types, or other internal implementation details. It does not by itself yield authentication bypass, secret disclosure (salts/tokens), or user-existence oracle beyond generic error-message leakage, since token lookups are parameterized and salts are not compared/logged in this path. Under the Chainlink bounty impact classification this is best categorized as a low-severity "information disclosure of non-critical internal details," not a critical/high authentication bypass or credential leak.

### Likelihood Explanation
Exploitability is uncertain and constrained: it requires the attacker to first cause a *non*-`sql.ErrNoRows`, non-`ErrUserSessionExpired` database error from `FindUserByAPIToken` (e.g., a column length violation, DB timeout, or connection error). Whether the `token_key` column enforces a length constraint that a client-controlled header value can violate could not be conclusively confirmed from the available migration/schema excerpts within the tool budget — the `users` table's `token_key` column definition was not found in the truncated `0001_initial.sql` output. Absent a concretely reachable, attacker-triggerable non-`ErrNoRows` DB error, this remains a plausible but unconfirmed defense-in-depth gap rather than a demonstrated, reliably reproducible vulnerability.

### Recommendation
In `AuthenticateByToken` (and analogous LDAP/OIDC implementations), treat *any* error from `FindUserByAPIToken` as a generic auth failure for the client-facing response (return `auth.ErrorAuthFailed` or a wrapped/redacted error), while logging the original raw error server-side for diagnostics. More generally, `jsonAPIError` should never emit `err.Error()` for errors that are not already sanitized/user-safe types (e.g., only allow `*models.JSONAPIErrors` or a small allow-listed set of sentinel errors to pass through verbatim, and default to a static "internal error" message otherwise).

### Proof of Concept
Because the concrete DB error trigger could not be verified within the available context, the PoC below documents the test needed to validate/refute the issue:
1. Implement a `stubAuthProvider.FindUserByAPIToken` (as used in `core/web/auth/auth_test.go`) that returns a non-`sql.ErrNoRows`/non-`ErrUserSessionExpired` error, e.g. `errors.New("pq: value too long for type character varying(50)")`.
2. Wire it into `router := gin.New(); router.Use(webauth.Authenticate(authr, webauth.AuthenticateByToken))` as in `TestAuthenticateByToken_AuthFailed`.
3. Send a request with `X-API-KEY`/`X-API-SECRET` headers set, and assert the response body via `httptest.NewRecorder()`.
4. **Expected (if vulnerable):** response body contains the literal raw error string (`"pq: value too long..."`) instead of a generic message — confirming leakage.
5. **Expected (if not exploitable in practice):** confirm whether any code path in `core/sessions/localauth/orm.go`, `core/sessions/ldapauth/ldap.go`, or `core/sessions/oidcauth/oidc.go` can actually be driven by attacker-controlled header input into a non-`ErrNoRows` DB error (e.g., verify `token_key` column type/constraints in the full, non-truncated migration files) — a Devin session with full filesystem access should be used to confirm this schema detail before treating this as a confirmed, immediately reproducible bug.

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

**File:** core/sessions/localauth/orm.go (L48-53)
```go
// FindUserByAPIToken will attempt to return an API user via the user's table token_key column.
func (o *orm) FindUserByAPIToken(ctx context.Context, apiToken string) (user sessions.User, err error) {
	sql := "SELECT * FROM users WHERE token_key = $1"
	err = o.ds.GetContext(ctx, &user, sql, apiToken)
	return
}
```
