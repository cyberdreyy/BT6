### Title
User/Admin Account Enumeration via Distinguishable Error Messages in CreateSession - ([File: core/sessions/localauth/orm.go])

### Summary
The `CreateSession` function in `core/sessions/localauth/orm.go` returns distinct, literal error strings ("Invalid email" vs "Invalid password") depending on whether the submitted email exists in the `users` table. These raw error strings are propagated unmodified into the HTTP JSON response body returned to an unauthenticated caller of `POST /sessions`, allowing account enumeration.

### Finding Description
`CreateSession` first looks up the user by email via `FindUser`, then performs a constant-time email comparison, returning `pkgerrors.New("Invalid email")` on mismatch, and only afterward checks the password, returning `pkgerrors.New("Invalid password")` on mismatch: [1](#0-0) 

The `POST /sessions` route is registered in the unauthenticated route group (`unauth.POST("/sessions", sc.Create)`), so no prior authentication is required to reach this code: [2](#0-1) 

`SessionsController.Create` forwards the error returned by `CreateSession` directly to `jsonAPIError` with a 401 status: [3](#0-2) 

`jsonAPIError` serializes `err.Error()` verbatim into the JSON API response body sent back to the client: [4](#0-3) 

As a result, an unauthenticated attacker submitting `POST /sessions` with a candidate email and an arbitrary password receives `"Invalid email"` in the response body if the email does not exist, and `"Invalid password"` if it does — directly disclosing valid account existence without needing to distinguish via timing. The comment in the code ("Do email and password check first to prevent extra database look up for MFA tokens leaking...") shows the timing/DB-lookup side channel was considered, but the literal error-message differentiation was not addressed. Note this differs slightly from the audit prompt's framing (it's not primarily a timing side channel but a direct message-content leak that is even easier to exploit), and `TestPassword` (`core/sessions/localauth/orm.go` lines 309-318) has the same message-differentiation pattern but is not reachable from the unauthenticated `/sessions` route, so it is out of scope here as an unauthenticated primitive but shares the same root cause. [5](#0-4) 

### Impact Explanation
This is an account/email enumeration vulnerability, a reconnaissance precursor for credential-stuffing or targeted phishing/brute-force campaigns against known valid admin accounts of the Chainlink node operator UI. It does not by itself grant authentication bypass or session creation, but it directly discloses account existence, which is disallowed by the stated invariant ("authentication must not disclose account existence").

### Likelihood Explanation
No credentials or privileges are required — any unauthenticated network client that can reach the node's `/sessions` HTTP endpoint can perform this attack. The endpoint is only protected by a generic unauthenticated rate limiter (`rl.UnauthenticatedPeriod()`/`rl.Unauthenticated()`), not by response normalization: [6](#0-5)  This makes the attack trivially repeatable, limited only by the configured rate limit, and fully automatable for enumerating a list of candidate emails.

### Recommendation
Return a single generic, identical error message and status code (e.g., "invalid credentials") for both the "email not found" and "password mismatch" branches in `CreateSession`, and ensure equivalent work (e.g., always perform a password-hash comparison against a dummy hash when the user is not found) to minimize timing differences. Avoid embedding branch-specific literal strings that reach the client via `jsonAPIError`; keep detailed audit information (`AuthLoginFailedEmail`/`AuthLoginFailedPassword`) only in the audit log, not in the HTTP response.

### Proof of Concept
Add a handler-level integration test in `core/web/sessions_controller_test.go` extending `TestSessionsController_Create`:
1. Create a user with a known password.
2. POST `/sessions` with `{"email": "<existing>@test.net", "password": "wrongpass"}` and capture the response body detail string.
3. POST `/sessions` with `{"email": "nonexistent@test.net", "password": "wrongpass"}` and capture the response body detail string.
4. Assert both requests return the same HTTP status code (401) — already true — AND assert `require.Equal(t, resp1BodyDetail, resp2BodyDetail)`. This assertion currently fails because body 1 contains `"Invalid password"` and body 2 contains `"Invalid email"`, proving the enumeration vector.

### Citations

**File:** core/sessions/localauth/orm.go (L144-162)
```go
func (o *orm) CreateSession(ctx context.Context, sr sessions.SessionRequest) (string, error) {
	user, err := o.FindUser(ctx, sr.Email)
	if err != nil {
		return "", err
	}
	lggr := o.lggr.With("user", user.Email)
	lggr.Debugw("Found user")

	// Do email and password check first to prevent extra database look up
	// for MFA tokens leaking if an account has MFA tokens or not.
	if !constantTimeEmailCompare(strings.ToLower(sr.Email), strings.ToLower(user.Email)) {
		o.auditLogger.Audit(audit.AuthLoginFailedEmail, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid email")
	}

	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		o.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid password")
	}
```

**File:** core/sessions/localauth/orm.go (L308-318)
```go
// TestPassword checks plaintext user provided password with hashed database password, returns nil if matched
func (o *orm) TestPassword(ctx context.Context, email string, password string) error {
	var hashedPassword string
	if err := o.ds.GetContext(ctx, &hashedPassword, "SELECT hashed_password FROM users WHERE lower(email) = lower($1)", email); err != nil {
		return pkgerrors.New("no matching user for provided email")
	}
	if !utils.CheckPasswordHash(password, hashedPassword) {
		return pkgerrors.New("passwords don't match")
	}
	return nil
}
```

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
