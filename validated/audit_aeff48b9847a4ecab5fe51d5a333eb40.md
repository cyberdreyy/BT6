### Title
Unauthenticated auth-failure paths leak raw internal error strings to the client - ([File: core/web/auth/auth.go], [File: core/web/auth/helpers.go])

### Finding Description
`jsonAPIError` (`core/web/auth/helpers.go:15-23`) serializes any non-`*models.JSONAPIErrors` error directly with `models.NewJSONAPIErrorsWith(err.Error())`, placing the raw Go error string into the HTTP response body. [1](#0-0) 

`AuthenticateBySession` (`core/web/auth/auth.go:55-71`) calls `authr.AuthorizedUserWithSession(ctx, sessionID)` and, on any failure, returns the error **unmodified** — there is no normalization to `auth.ErrorAuthFailed` for anything other than the missing-session-cookie case. [2](#0-1) 

`AuthenticateByToken` (`core/web/auth/auth.go:78-112`) does normalize `sql.ErrNoRows` and `clsessions.ErrUserSessionExpired` from `FindUserByAPIToken` into `auth.ErrorAuthFailed`, but any *other* error returned by `FindUserByAPIToken` (line 98) or by `clsessions.AuthenticateUserByToken` (line 103) is returned as-is, unnormalized. [3](#0-2) 

These raw errors then propagate through `Authenticate()` (`core/web/auth/auth.go:157-175`), which only special-cases `auth.ErrorAuthFailed` for retrying additional auth methods but forwards any other error directly into `jsonAPIError(c, http.StatusUnauthorized, err)` at line 168. [4](#0-3) 

Since the underlying data-store implementations (e.g. `core/sessions/localauth/orm.go`'s `AuthorizedUserWithSession`, `oidcauth`, `ldapauth`) can return arbitrary DB/driver errors (connection failures, query errors, etc.) that are not `sql.ErrNoRows`, those raw messages would flow unfiltered to an unauthenticated caller's JSON response, unlike the generic `"not a valid session"` / `"Unauthorized"` strings used elsewhere in `RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole`.

### Impact Explanation
An unauthenticated attacker who can trigger a non-`sql.ErrNoRows` failure in the authentication data path (e.g., transient DB errors, malformed session ID causing a query error, or other store-specific failure) would receive that error's raw string in the JSON response body. This can disclose internal implementation details (query fragments, driver/DB error text, internal state) to an unauthenticated party — an information-disclosure issue that aids fingerprinting/enumeration, though it does not by itself grant authentication bypass or fund movement. This falls into the "information disclosure" bounty class, not a critical/high-severity credential or fund-loss class.

### Likelihood Explanation
No credentials required — the attacker only needs to cause the auth provider to return a non-nil, non-`ErrorAuthFailed` error, which is plausible under transient backend failures, unusual session ID formats hitting a different code path, or store implementations (OIDC/LDAP) that return richer/wrapped errors on failure. The exact triggerability depends on the concrete error types returned by `AuthorizedUserWithSession`/`FindUserByAPIToken`/`AuthenticateUserByToken` implementations, which were not fully inspected within the available tool budget — this remains a plausible but not fully proven-triggerable condition for arbitrary content (vs. simple `sql.ErrNoRows`, which is already normalized).

### Recommendation
In `AuthenticateBySession` and `AuthenticateByToken`, normalize **all** authentication-provider errors to `auth.ErrorAuthFailed` (or another fixed generic sentinel) before returning, logging the original error server-side instead. Alternatively, harden `jsonAPIError` so that for 401/403 status codes it never emits `err.Error()` verbatim — only a fixed generic message — regardless of the wrapped error type, unless the error is explicitly an `*models.JSONAPIErrors` constructed for public consumption.

### Proof of Concept
1. In `core/web/auth/auth_test.go`, add a mock `Authenticator` whose `AuthorizedUserWithSession` returns a non-`sql.ErrNoRows` error (e.g., `errors.New("pq: connection reset by peer")`).
2. Invoke `AuthenticateBySession` and then `Authenticate(store, AuthenticateBySession)` in a `gin.Context` test harness with a POST request carrying an arbitrary session cookie.
3. Assert the HTTP response body: currently it would contain `"pq: connection reset by peer"` in the JSON error detail — assert this **should instead** be a fixed generic string (e.g., `"Unauthorized"`), failing the current implementation.
4. Repeat for `AuthenticateByToken` with `FindUserByAPIToken` returning a non-`sql.ErrNoRows`/non-`ErrUserSessionExpired` error and for `AuthenticateUserByToken` returning an error, confirming the raw error text also leaks unless normalized.

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

**File:** core/web/auth/auth.go (L55-71)
```go
func AuthenticateBySession(c *gin.Context, authr Authenticator) error {
	ctx := c.Request.Context()
	session := sessions.Default(c)
	sessionID, ok := session.Get(SessionIDKey).(string)
	if !ok {
		return auth.ErrorAuthFailed
	}

	user, err := authr.AuthorizedUserWithSession(ctx, sessionID)
	if err != nil {
		return err
	}

	c.Set(SessionUserKey, &user)

	return nil
}
```

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
