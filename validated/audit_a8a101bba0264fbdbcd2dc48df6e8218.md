### Title
Local admin email enumeration via distinguishable error responses in `oidcAuthenticator.localLoginFallback` - ([File: core/sessions/oidcauth/oidc.go])

### Summary
`localLoginFallback` queries the `users` table directly and, on a missing row, returns the raw `sql.ErrNoRows`-derived error unmodified, while a wrong-password attempt against an existing user returns the distinct string `"invalid password"`. Since `POST /sessions` propagates `err.Error()` verbatim to the HTTP client, an unauthenticated attacker can distinguish existing vs non-existing local admin emails from the response body, not merely from timing.

### Finding Description
`CreateSession` (oidc.go:412-439) calls `localLoginFallback` (oidc.go:580-597) for every unauthenticated `POST /sessions` request when the OIDC auth provider is active [1](#0-0) . Unlike `FindUser` in the same file, which normalizes `sql.ErrNoRows` into a generic `"user not found"` error [2](#0-1) , `localLoginFallback` queries the DB directly and returns the raw driver error unmodified when no row matches: [3](#0-2) 

That raw error (or a wrapped `sql.ErrNoRows`) differs textually from the `"invalid password"` error returned when the email exists but the password is wrong. Both errors flow to `SessionsController.Create`, which calls `jsonAPIError(c, http.StatusUnauthorized, err)` for any failure [4](#0-3) , and `jsonAPIError` serializes `err.Error()` directly into the JSON response body when the error isn't a `*models.JSONAPIErrors`: [5](#0-4) 

Thus the HTTP response body content (not just timing) differs between "email doesn't exist" (raw SQL error text) and "email exists, wrong password" (`"invalid password"`), giving a reliable oracle. The `constantTimeEmailCompare` call at oidc.go:586 is a no-op safeguard here because the SQL query already filters by `lower(email)=lower($1)`, so it can never actually fail once a row is returned — it does not mitigate the pre-query lookup leak.

### Impact Explanation
An unauthenticated client can enumerate valid local admin emails in the `users` table by sending candidate emails to `POST /sessions` and inspecting the returned error text, distinguishing "no such user" from "user exists, bad password." This narrows brute-force/credential-stuffing targeting to real local admin accounts, matching the "account enumeration" impact class scoped in this question (assisting brute-force targeting of local admin credentials).

### Likelihood Explanation
No credentials or privileges are required — this is exploitable by any unauthenticated network client able to reach `POST /sessions`, but only when the node is configured with the OIDC authentication provider (the vulnerable `localLoginFallback` code path is only invoked for that provider) [6](#0-5) . The rate limiter on `/sessions` (oidc.go router config) throttles but does not prevent enumeration, only slows it. Feasibility is high and fully repeatable via scripted requests with varying emails.

### Recommendation
In `localLoginFallback`, normalize the "no user found" case to the same generic error (e.g., `"invalid credentials"`) used for wrong-password, and avoid returning raw `sql.ErrNoRows`/driver errors to the caller — mirror the sanitization already done in `FindUser`. Additionally, consider returning a single generic error/message for all authentication failure branches in `CreateSession`/`SessionsController.Create` so the HTTP response body cannot be used to distinguish failure reasons, complementing (not replacing) the existing constant-time comparison.

### Proof of Concept
Go table test in `core/sessions/oidcauth/oidc_test.go`:
1. Seed one user row (e.g., `admin@example.com`) with a known password hash.
2. Call `localLoginFallback` with:
   - `{Email: "admin@example.com", Password: "wrongpass"}` → expect error message exactly `"invalid password"`.
   - `{Email: "doesnotexist@example.com", Password: "whatever"}` → expect error to be (or wrap) `sql.ErrNoRows`, with `err.Error()` containing `"no rows"` rather than `"invalid password"` or `"invalid email"`.
3. Assert the two error strings differ, proving distinguishability.
4. Handler-level integration test extending `TestSessionsController_Create` in `core/web/sessions_controller_test.go`: POST to `/sessions` with an existing email + wrong password vs a non-existing email + any password, and assert the JSON error body text differs (in addition to identical `401` status codes), demonstrating the response-body oracle available to an unauthenticated caller.

### Citations

**File:** core/web/router.go (L207-215)
```go
func sessionRoutes(app chainlink.Application, r *gin.RouterGroup) {
	config := app.GetConfig()
	rl := config.WebServer().RateLimit()
	unauth := r.Group("/", rateLimiter(
		rl.UnauthenticatedPeriod(),
		rl.Unauthenticated(),
	))
	sc := NewSessionsController(app)
	unauth.POST("/sessions", sc.Create)
```

**File:** core/sessions/oidcauth/oidc.go (L279-295)
```go
func (oi *oidcAuthenticator) FindUser(ctx context.Context, email string) (clsessions.User, error) {
	email = strings.ToLower(email)

	var foundUser clsessions.User

	if err := oi.ds.GetContext(ctx, &foundUser, SQLSelectUserbyEmail, email); err != nil {
		// If the error is not that no local user was found, log and exit
		if errors.Is(err, sql.ErrNoRows) {
			return clsessions.User{}, errors.New("user not found")
		}

		oi.lggr.Errorf("error searching users table: %v", err)
		return clsessions.User{}, errors.New("error finding user")
	}

	return foundUser, nil
}
```

**File:** core/sessions/oidcauth/oidc.go (L412-416)
```go
func (oi *oidcAuthenticator) CreateSession(ctx context.Context, sr clsessions.SessionRequest) (string, error) {
	foundUser, err := oi.localLoginFallback(ctx, sr)
	if err != nil {
		return "", err
	}
```

**File:** core/sessions/oidcauth/oidc.go (L580-597)
```go
func (oi *oidcAuthenticator) localLoginFallback(ctx context.Context, sr clsessions.SessionRequest) (clsessions.User, error) {
	var user clsessions.User
	err := oi.ds.GetContext(ctx, &user, SQLSelectUserbyEmail, sr.Email)
	if err != nil {
		return user, err
	}
	if !constantTimeEmailCompare(strings.ToLower(sr.Email), strings.ToLower(user.Email)) {
		oi.auditLogger.Audit(audit.AuthLoginFailedEmail, map[string]any{"email": sr.Email})
		return user, errors.New("invalid email")
	}

	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		oi.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return user, errors.New("invalid password")
	}

	return user, nil
}
```

**File:** core/web/sessions_controller.go (L56-60)
```go
	sid, err := sc.App.AuthenticationProvider().CreateSession(ctx, sr)
	if err != nil {
		jsonAPIError(c, http.StatusUnauthorized, err)
		return
	}
```

**File:** core/web/helpers.go (L21-29)
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
