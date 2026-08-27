### Title
Internal DB/driver error details leaked to unauthenticated clients via `AuthenticateExternalInitiator` error wrapping - ([File: core/web/auth/auth.go])

### Summary
When `store.FindExternalInitiator` returns a non-`sql.ErrNoRows` error (e.g., a driver-level error such as an invalid UTF‑8 encoding error when the `AccessKey` header contains malformed bytes), `AuthenticateExternalInitiator` wraps it with `errors.Wrap(err, "finding external initiator")` and returns it up through `Authenticate`, which passes it unmodified to `jsonAPIError`. `jsonAPIError` serializes `err.Error()` directly into the JSON response body sent back to the (still unauthenticated) caller, exposing the underlying database/driver error text.

### Finding Description
The authentication middleware chain is:

- `AuthenticateExternalInitiator` reads `static.ExternalInitiatorAccessKeyHeader`/`SecretHeader` directly from client-controlled request headers and passes the `AccessKey` as a raw SQL parameter to `FindExternalInitiator`: [1](#0-0) 
- The ORM implementations execute `SELECT * FROM external_initiators WHERE access_key = $1` using the untrusted header value directly as a bind parameter, with no upfront validation (e.g. no UTF‑8 check) before it is sent to the SQL driver: [2](#0-1) 
- If the driver returns anything other than `sql.ErrNoRows` (for example a Postgres driver error such as `invalid byte sequence for encoding "UTF8"` triggered by malformed/binary bytes in the header), the code does **not** treat this generically — it wraps and propagates the raw error: `return errors.Wrap(err, "finding external initiator")`. [3](#0-2) 
- `Authenticate` then forwards this wrapped error straight into `jsonAPIError(c, http.StatusUnauthorized, err)`: [4](#0-3) 
- `jsonAPIError` puts `err.Error()` verbatim into the JSON response body returned to the client, with no redaction or generic-message substitution for non-`JSONAPIErrors` error types: [5](#0-4) 

Go's `net/http` header parsing only rejects header values containing NUL, CR, or LF bytes; it does not enforce UTF‑8 validity on header field values, so an unauthenticated attacker can send a header value containing invalid UTF‑8 byte sequences that reach the SQL driver as a bind parameter. Since the destination column is `text`/`varchar` in a UTF‑8 encoded Postgres database, this can produce a driver-level encoding error (not `sql.ErrNoRows`), which is exactly the class of error this code path is not equipped to suppress.

### Impact Explanation
This is an information-disclosure issue (SECRET_CONFINEMENT violation): an unauthenticated attacker can force a database/driver error and receive its raw text (e.g., driver name, encoding details, potentially query fragments depending on driver error verbosity) in the HTTP 401 response body. While the attacker doesn't gain direct credential or fund access, this leaks internal implementation details (driver in use, encoding, that a raw parameterized SQL query is being executed) that aid further reconnaissance/attack planning against the node's HTTP API, and is inconsistent with the intended generic "auth failed" behavior for the `sql.ErrNoRows` case handled just above it.

### Likelihood Explanation
- No privileges or credentials of any kind are required — this is reachable at the unauthenticated `AuthenticateExternalInitiator` middleware stage, which runs on external-initiator-protected routes before any authentication succeeds.
- The attacker only needs to send an HTTP request with `static.ExternalInitiatorAccessKeyHeader` set to bytes that are valid as an HTTP header value (no CR/LF/NUL) but invalid as UTF‑8, which is trivial to construct.
- Feasibility depends on the specific SQL driver/Postgres configuration surfacing a distinct (non-`ErrNoRows`) error for invalid UTF‑8 parameters, which is a well-known behavior for `lib/pq`/`pgx` against UTF‑8-encoded Postgres databases.
- Repeatable: this can be sent repeatedly and deterministically once a byte sequence that triggers the encoding error is identified.

### Recommendation
In `AuthenticateExternalInitiator` (`core/web/auth/auth.go`), do not propagate the raw underlying error to the HTTP layer. Log the detailed error server-side and return a generic `auth.ErrorAuthFailed` (or another non-leaking sentinel error) for any error path, not just `sql.ErrNoRows`. More generally, `jsonAPIError` (and its duplicate in `core/web/helpers.go`) should not directly serialize arbitrary `error.Error()` output for internal/unexpected errors returned from authentication paths — only errors explicitly intended for client consumption (e.g., `models.JSONAPIErrors`, or a defined sentinel like `auth.ErrorAuthFailed`) should reach the response body.

### Proof of Concept
Handler-level integration test plan:
1. Set up a test Gin router wired with `Authenticate(store, AuthenticateExternalInitiator)` in front of a protected route, using a real (or a fake driver that mimics Postgres UTF‑8 encoding errors) `Authenticator`/`ORM` backed by a Postgres test DB.
2. Send an HTTP request to the protected route with header `static.ExternalInitiatorAccessKeyHeader` set to a byte sequence that is valid per RFC 7230 (no CR/LF/NUL) but is invalid UTF‑8 (e.g., a lone continuation byte `0x80`).
3. Assert the response status is `401 Unauthorized`.
4. Assert the JSON response body's error message equals a generic string (e.g., matches `auth.ErrorAuthFailed.Error()`) and does **not** contain substrings like `"finding external initiator"`, `"invalid byte sequence"`, `"UTF8"`, `pq:`, or any SQL statement fragment such as `external_initiators` or `access_key`.
5. As a regression baseline, confirm the current (vulnerable) behavior first reproduces the leak (response body contains the wrapped driver error text) before the fix, then confirm it no longer does after applying the recommended generic-error handling.

### Citations

**File:** core/web/auth/auth.go (L119-133)
```go
func AuthenticateExternalInitiator(c *gin.Context, store Authenticator) error {
	ctx := c.Request.Context()
	eia := &auth.Token{
		AccessKey: c.GetHeader(static.ExternalInitiatorAccessKeyHeader),
		Secret:    c.GetHeader(static.ExternalInitiatorSecretHeader),
	}

	ei, err := store.FindExternalInitiator(ctx, eia)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return auth.ErrorAuthFailed
		}

		return errors.Wrap(err, "finding external initiator")
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

**File:** core/bridges/orm.go (L262-267)
```go
// FindExternalInitiator finds an external initiator given an authentication request
func (o *orm) FindExternalInitiator(ctx context.Context, eia *auth.Token) (*ExternalInitiator, error) {
	exi := &ExternalInitiator{}
	err := o.ds.GetContext(ctx, exi, `SELECT * FROM external_initiators WHERE access_key = $1`, eia.AccessKey)
	return exi, err
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
