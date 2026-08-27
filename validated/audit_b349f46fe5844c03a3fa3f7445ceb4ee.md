Confirmed: `jsonAPIError` at [1](#0-0)  calls `err.Error()` and returns it directly in the JSON response body when the error is not a `*models.JSONAPIErrors`. Since `errors.Wrap(err, "finding external initiator")` from `AuthenticateExternalInitiator` at [2](#0-1)  is a plain wrapped error, it falls into this default branch and its full `.Error()` string — which is `"finding external initiator: " + <underlying DB error text>` — is serialized straight into the HTTP response body via `models.NewJSONAPIErrorsWith(err.Error())`.

This is reached through the unauthenticated route group `userOrEI` at [3](#0-2) , which applies `auth.AuthenticateExternalInitiator` before any credential is verified, so an attacker with no credentials can hit `GET /v2/ping` or `POST /v2/jobs/:ID/runs` with crafted `X-Chainlink-EA-AccessKey` / `X-Chainlink-EA-Secret` headers and reach this code path.

However, I could not locate the actual `FindExternalInitiator` ORM implementation content in the index (only reference lists in `core/bridges/orm.go`, `core/bridges/mocks/orm.go`, etc. were found, not their bodies), so I cannot confirm what non-`sql.ErrNoRows` errors are realistically achievable from attacker-controlled header values (e.g., truncation/constraint errors, driver-level errors) or what exact text they'd contain. This limits confidence in constructing a concrete PoC that forces a specific non-`ErrNoRows` DB error via crafted headers alone (e.g., an oversized `AccessKey` value truncated by a `VARCHAR` column constraint, or a malformed encoding causing a driver-level error). Given index size limits, a Devin session with full repo access would be needed to inspect `core/bridges/orm.go`'s `FindExternalInitiator` query and DB schema/constraints to confirm a concrete non-`ErrNoRows` failure trigger from external initiator headers.

### Title
Unauthenticated information disclosure via unwrapped DB error text returned to external-initiator auth failures - (File: core/web/auth/auth.go)

### Summary
`AuthenticateExternalInitiator` wraps any non-`sql.ErrNoRows` error from `store.FindExternalInitiator` with `errors.Wrap(err, "finding external initiator")` and returns it up through the `Authenticate` middleware, which passes it to `jsonAPIError`. Because the wrapped error is not a `*models.JSONAPIErrors`, `jsonAPIError` serializes the raw `err.Error()` string — including the underlying database error text — directly into the JSON response body sent to the unauthenticated caller.

### Finding Description
The route group `userOrEI` in [3](#0-2)  applies `auth.AuthenticateExternalInitiator` as one of several auth methods, without requiring prior authentication, for `GET /v2/ping` and `POST /v2/jobs/:ID/runs`. Inside `AuthenticateExternalInitiator` ( [4](#0-3) ), the attacker-supplied `AccessKey`/`Secret` headers are passed to `store.FindExternalInitiator`. If the returned error is `sql.ErrNoRows`, it's converted to the generic `auth.ErrorAuthFailed`; for any other error, it is wrapped with `errors.Wrap(err, "finding external initiator")` and returned as-is. The `Authenticate` middleware ( [5](#0-4) ) then calls `jsonAPIError(c, http.StatusUnauthorized, err)`, and since this wrapped error does not implement/wrap `*models.JSONAPIErrors`, `jsonAPIError` falls to `c.JSON(statusCode, models.NewJSONAPIErrorsWith(err.Error()))` ( [1](#0-0) ), placing the raw underlying error text (potentially containing DB driver messages, constraint names, or connection details) into the HTTP response body returned to the unauthenticated attacker.

### Impact Explanation
This matches Chainlink's "information disclosure aiding further attacks" impact class: leaking internal database error text (e.g., column/constraint names, driver-specific messages) to an unauthenticated caller can help an attacker map internal schema/implementation details useful for crafting further attacks (e.g., SQL injection reconnaissance, DoS targeting specific failure modes), though it does not by itself grant authentication bypass or credential exposure.

### Likelihood Explanation
Preconditions: attacker needs no credentials, only the ability to send an HTTP request with crafted `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers to a route protected by `AuthenticateExternalInitiator` (e.g., `GET /v2/ping`). The feasibility of actually forcing a non-`ErrNoRows` DB error from `FindExternalInitiator` using only header values is uncertain from the available index — it would depend on the underlying query/DB driver behavior (e.g., value length limits, encoding issues) which I could not verify due to index limitations on `core/bridges/orm.go`. If any non-`ErrNoRows` error is reachable (including transient errors like connection timeouts, context cancellation, etc.), the leak is deterministic and repeatable.

### Recommendation
In `AuthenticateExternalInitiator`, do not propagate the raw wrapped DB error to the HTTP layer. Log the detailed error server-side (e.g., via the app logger) and return a generic `auth.ErrorAuthFailed` (or a new generic internal-error sentinel) to the middleware/`jsonAPIError`, consistent with how `sql.ErrNoRows` is already handled.

### Proof of Concept
1. Add a Go unit test for `auth.AuthenticateExternalInitiator` using a mock `Authenticator`/`FindExternalInitiator` implementation (from `core/bridges/mocks/orm.go` or `core/sessions/mocks/authentication_provider.go`) that returns a non-`sql.ErrNoRows` error, e.g., `errors.New("dial tcp 127.0.0.1:5432: connect: connection refused")` or `errors.New("pq: value too long for type character varying(64)")`.
2. Build a `gin.Context` with `httptest.NewRecorder()` wrapping a request carrying `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers, and route it through `auth.Authenticate(mockStore, auth.AuthenticateExternalInitiator)`.
3. Assert the HTTP status is 401 and decode the JSON body into `models.JSONAPIErrors`; assert that the error detail string does NOT contain the substrings from the underlying mock error (e.g., "connection refused", "value too long", "pq:"), which the test should currently fail against, demonstrating the leak.
4. Additionally verify the fix: after patching `AuthenticateExternalInitiator` to return a generic error for non-`ErrNoRows` cases, re-run the test and assert the JSON body only contains a generic message (matching existing 401 behavior for bad credentials).

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

**File:** core/web/router.go (L450-456)
```go
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
```
