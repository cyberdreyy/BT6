### Title
Unauthenticated internal error text disclosure via `AuthenticateExternalInitiator` short-circuit in `auth.Authenticate` loop - ([File: core/web/auth/auth.go])

### Summary
When `FindExternalInitiator` returns any error other than `sql.ErrNoRows`, `AuthenticateExternalInitiator` wraps and returns that raw error, which is a non-`auth.ErrorAuthFailed` value. This causes `Authenticate`'s loop to break immediately (skipping any subsequent auth methods) and the wrapped internal error text is serialized verbatim into the JSON response sent to the unauthenticated caller.

### Finding Description
`Authenticate` iterates over the configured `authMethod`s and only continues to the next method if the previous one returned `auth.ErrorAuthFailed`; any other error breaks the loop immediately: `Authenticate` loop and break condition [1](#0-0) .

`AuthenticateExternalInitiator` calls `store.FindExternalInitiator(ctx, eia)` with attacker-controlled headers (`X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` equivalents), and only normalizes `sql.ErrNoRows` to `auth.ErrorAuthFailed` — any other error (e.g., a malformed access key causing a DB-level error, decoding error, or other backend failure) is wrapped with `errors.Wrap(err, "finding external initiator")` and returned as-is: [2](#0-1) .

Because this returned error is not `auth.ErrorAuthFailed`, the loop in `Authenticate` breaks without trying `AuthenticateByToken` or `AuthenticateBySession`, and the error is passed straight to `jsonAPIError`: [3](#0-2) .

`jsonAPIError` serializes `err.Error()` directly into the JSON body returned to the client, with no filtering or generic replacement of message text: [4](#0-3) . Since `errors.Wrap` preserves the full message chain of the underlying cause, whatever text the underlying store/DB layer produces (e.g., driver-level error strings, potentially including query or schema details) is propagated to the unauthenticated caller.

Important scoping clarification: this does **not** allow the request to become authenticated. Because `err` remains non-nil, `Authenticate` calls `c.Abort()` and returns HTTP 401 — no session/user/EI context is set, and `RequiresRunRole`/`RequiresEditRole`/etc. are never reached. So the "authenticate on userOrEI group despite an invalid token" scenario described in the question does not occur — the request is correctly rejected. The concrete, exploitable issue is narrower: **information disclosure of raw backend error text to an unauthenticated caller**, violating the stated invariant that failed-auth error responses must not leak backend internals.

### Impact Explanation
An unauthenticated attacker can trigger error paths in `FindExternalInitiator` (or theoretically the analogous non-`ErrorAuthFailed`/non-`ErrNoRows` errors in `AuthenticateByToken`'s `FindUserByAPIToken` call, per the same `errors.Wrap`-less passthrough pattern at lines 93-99) and receive the verbatim internal error string in the 401 JSON response. This matches Chainlink's "information disclosure" bounty class — it can leak implementation/schema details useful for further attacks — but does **not** constitute an authentication or authorization bypass, since the request is still rejected with 401 and no privileged context is granted.

### Likelihood Explanation
Reaching the vulnerable code path only requires an unauthenticated POST to any endpoint protected by `auth.Authenticate(..., auth.AuthenticateExternalInitiator, ...)` (e.g., `/v2/jobs/:ID/runs`) with a malformed or well-formed-but-erroring EI access key header. No credentials are required — this is directly reachable by any unauthenticated caller. The exact content of the leaked message depends on what error `FindExternalInitiator`'s backend implementation can realistically produce for attacker input, which was not fully verified in this pass (the implementation of `FindExternalInitiator` itself was not inspected here) — so the practical sensitivity of the disclosed text is unconfirmed.

### Recommendation
In `AuthenticateExternalInitiator` (and equivalently `AuthenticateByToken`), do not propagate raw underlying errors to the HTTP response. Log the wrapped error server-side, but return a generic `auth.ErrorAuthFailed` (or a fixed generic "authentication failed" error) for any non-nil error from the store lookup, so the client-facing message from `jsonAPIError` never contains backend-internal error text. Additionally, consider having `Authenticate` continue trying subsequent auth methods even on non-`ErrorAuthFailed` errors (returning the least-informative error message) rather than short-circuiting the loop, to reduce the risk of leaking which specific auth method failed and why.

### Proof of Concept
1. Unit test `TestAuthenticateExternalInitiator_LeaksInternalError` in `core/web/auth`:
   - Create a mock `Authenticator` whose `FindExternalInitiator` returns a non-`sql.ErrNoRows` error, e.g. `errors.New("pq: syntax error near externalinitiators.access_key")`.
   - Call `AuthenticateExternalInitiator(c, mockAuthr)` directly and assert the returned error's `Error()` string contains `"finding external initiator: pq: syntax error..."`.
2. Handler-level integration test:
   - Build a gin router with `auth.Authenticate(store, auth.AuthenticateExternalInitiator, auth.AuthenticateByToken, auth.AuthenticateBySession)` protecting `POST /v2/jobs/:ID/runs`.
   - Inject a mock/store that returns a non-`ErrNoRows` error from `FindExternalInitiator` when given a crafted EI access key header.
   - Send `POST /v2/jobs/1/runs` with the malformed EI headers and no session cookie.
   - Assert response status is 401, and assert response JSON body's `errors[0].detail` (via `models.JSONAPIErrorsWith`) contains the raw wrapped error text (e.g., `"finding external initiator: ..."`), confirming leakage, while also asserting no `SessionUserKey`/`SessionExternalInitiatorKey` was set in the gin context (confirming no authentication bypass occurred).

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
