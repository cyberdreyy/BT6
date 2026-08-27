### Title
Non-ErrorAuthFailed errors from AuthenticateExternalInitiator short-circuit the auth chain and leak wrapped internal error text to unauthenticated clients - ([File: core/web/auth/auth.go])

### Summary
`Authenticate` iterates its `authMethod` list and only continues to the next method when the previous one returns `auth.ErrorAuthFailed`; any other error breaks the loop immediately [1](#0-0) . `AuthenticateExternalInitiator` wraps any non-`sql.ErrNoRows` error from `FindExternalInitiator` with `errors.Wrap(err, "finding external initiator")` and returns it as-is [2](#0-1) , and `jsonAPIError` serializes `err.Error()` (the wrapped message, including the underlying error text) directly into the JSON response body [3](#0-2) .

### Finding Description
When a route is wired as `auth.Authenticate(store, auth.AuthenticateExternalInitiator, auth.AuthenticateBySession)`, an attacker sends a request with EI headers (`X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret`) that route to a `FindExternalInitiator` call which returns any DB error other than `sql.ErrNoRows` (e.g., driver/connection error, malformed query error, context cancellation, etc.). `AuthenticateExternalInitiator` wraps this as `errors.Wrap(err, "finding external initiator")` and returns it — not `auth.ErrorAuthFailed` [2](#0-1) .

`Authenticate`'s loop checks `!errors.Is(err, auth.ErrorAuthFailed)`; since the wrapped error is not `ErrorAuthFailed`, the loop `break`s immediately, so `AuthenticateBySession` is never invoked at all [4](#0-3) . The handler then calls `jsonAPIError(c, http.StatusUnauthorized, err)`, and because the wrapped error is a plain `error` (not a `*models.JSONAPIErrors`), `jsonAPIError` falls through to `c.JSON(statusCode, models.NewJSONAPIErrorsWith(err.Error()))`, placing the full wrapped error string — including the underlying DB/driver error text — into the HTTP response body returned to the unauthenticated caller [3](#0-2) .

Regarding the specific "credential-oracle" framing in the question: because the loop breaks before `AuthenticateBySession` runs, the resulting 401 carries no information at all about whether the session method would have succeeded — the two paths are never both evaluated in this scenario, so there is no comparative signal leaked between "EI auth" and "session auth" outcomes. However, the actual, concrete issue is a genuine information-disclosure bug: the raw wrapped error message (which can include internal database/driver error text) is returned verbatim to an unauthenticated client, which is a violation of the principle that authentication failure responses should not include internal implementation details of the failure.

### Impact Explanation
This is a low-severity information disclosure: an unauthenticated attacker can potentially learn implementation details about the backing datastore (e.g., driver-specific error strings, connection state, or malformed-query messages) whenever `FindExternalInitiator` fails for a reason other than "not found." This does not by itself bypass authentication or authorization, expose secrets/keys, or allow impersonation — `AuthenticateExternalInitiator` never sets `SessionUserKey`/`SessionExternalInitiatorKey` unless the initiator is actually found and the HMAC check in `bridges.AuthenticateExternalInitiator` passes [5](#0-4) . The requested "credential-oracle" exploitation (distinguishing "closer to session-auth success") is not achievable here since the short-circuit prevents `AuthenticateBySession` from running at all in this error branch, so no differential signal about session validity is produced.

### Likelihood Explanation
Triggering a non-`sql.ErrNoRows` error from `FindExternalInitiator` under normal operation (a healthy DB, valid connection pool) is not something attacker-controlled headers alone can reliably induce; all current implementations (`core/bridges/orm.go`, `core/sessions/localauth/orm.go`, `core/sessions/ldapauth/ldap.go`, `core/sessions/oidcauth/oidc.go`) run a simple parameterized `SELECT * FROM external_initiators WHERE access_key = $1` [6](#0-5) , which would only fail with a non-`ErrNoRows` error under transient infrastructure conditions (DB outage, connection exhaustion, timeout) rather than from attacker-supplied header content. So while the code path is real, an attacker cannot deterministically trigger it purely via crafted requests without a pre-existing DB/infrastructure fault.

### Recommendation
- In `jsonAPIError`, avoid echoing raw internal error text for authentication failures; return a generic message (e.g., "internal error") for the client response while logging the detailed wrapped error server-side via `c.Error(err)`.
- In `AuthenticateExternalInitiator`, log the wrapped DB error internally but return a generic `auth.ErrorAuthFailed`-independent error (still non-`ErrorAuthFailed`, so behavior/short-circuit is preserved) without embedding the underlying error string, e.g. `return errors.New("error authenticating external initiator")` after logging `err` server-side.

### Proof of Concept
Go handler-level test plan:
1. Create a mock `Authenticator` (implementing `auth.Authenticator`) whose `FindExternalInitiator` returns a custom error, e.g. `errors.New("driver: bad connection")`, instead of `sql.ErrNoRows`.
2. Wire a `gin` test router with `auth.Authenticate(mockStore, auth.AuthenticateExternalInitiator, auth.AuthenticateBySession)` guarding a dummy handler.
3. Configure the mock's `AuthorizedUserWithSession` to return a valid user (to prove that, absent the short-circuit, session auth would have succeeded).
4. Send a request with `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers set to arbitrary values and a valid session cookie.
5. Assert: response status is `401`; response JSON body's error message contains the string `"driver: bad connection"` (proving raw internal error leakage); assert `AuthorizedUserWithSession` was never called (proving the short-circuit prevented session auth from being attempted, i.e., no comparative signal about session validity is exposed, but internal error text is exposed instead).

### Citations

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

**File:** core/web/auth/auth.go (L135-150)
```go
	ok, err := bridges.AuthenticateExternalInitiator(eia, ei)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}

	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
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

**File:** core/bridges/orm.go (L262-267)
```go
// FindExternalInitiator finds an external initiator given an authentication request
func (o *orm) FindExternalInitiator(ctx context.Context, eia *auth.Token) (*ExternalInitiator, error) {
	exi := &ExternalInitiator{}
	err := o.ds.GetContext(ctx, exi, `SELECT * FROM external_initiators WHERE access_key = $1`, eia.AccessKey)
	return exi, err
}
```
